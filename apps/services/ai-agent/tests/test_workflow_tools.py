"""[L1] ツールを MAF ネイティブの function calling に載せた配線を pin する (#320 / 段②④)。

MAF の `FunctionInvocationLayer` を**本物のまま**通す fake (tests/fakes.py) を使うので、
ここで見ているのは「テストの中の if 文」ではなくフレームワークが実際にやったこと。

無いと何が静かに通るか:
- registry の FunctionTool が `options["tools"]` に載らず、自前の分類プロンプトへ
  逆戻りする (真実源が 2 つに戻る)
- 既定オフのはずのスタブ題材が LLM に見えてしまう (#82 design-gate の PO 裁定 2)
- ツール実行の上限 (MAF の既定は**無制限**) を掛け忘れる
- ToolContext (ContextVar) が MAF の並列ツール実行を跨いで見えず、
  主体依存のツールだけが「Tool error」に化ける

ここで test しないこと:
- 承認の中断 / 再開 (test_workflow_approval.py)
- MAF の function calling 実装そのもの (フレームワークの領域)
"""

import logging

import pytest
from agent_framework import tool

from app.tools import _REGISTRY
from app.workflow import run_workflow
from tests.fakes import ScriptedChatClient, text_step, tool_call_step, tool_calls_step


class TestToolWiring:
    async def test_l1_registry_のツールが_llm_の_options_に載る(
        self, tools_enabled, session_repo, approval_repo
    ):
        client = ScriptedChatClient([text_step("うん。")])

        await run_workflow("s-wire", "やあ", session_repo, approval_repo, client)

        assert client.tool_names_seen == [sorted_names()]

    async def test_l1_既定では_llm_に_ツールを見せない(
        self, monkeypatch, session_repo, approval_repo
    ):
        # 無いと: フラグの既定が反転しても気づけない (#321 の裁定前にスタブ題材が出る)
        from app.config import get_settings

        monkeypatch.delenv("LLM_EXPOSED_TOOLS", raising=False)
        get_settings.cache_clear()
        try:
            client = ScriptedChatClient([text_step("うん。")])
            await run_workflow("s-off", "やあ", session_repo, approval_repo, client)
        finally:
            get_settings.cache_clear()

        assert client.tool_names_seen == [[]]

    async def test_l1_ツール実行の上限が_chat_client_に載る(
        self, tools_enabled, session_repo, approval_repo
    ):
        # 無いと: MAF の既定 (max_function_calls=None) のまま本番に出る
        client = ScriptedChatClient([text_step("うん。")])

        await run_workflow("s-limit", "やあ", session_repo, approval_repo, client)

        configuration = client.function_invocation_configuration
        assert configuration["max_function_calls"] is not None
        assert configuration["max_iterations"] is not None

    async def test_l1_ツールを使わない通常ターンの_llm_往復は_1_回(
        self, tools_enabled, session_repo, approval_repo
    ):
        # 無いと: 自前の分類プロンプト (v1 の CLASSIFY) が復活しても気づけない。
        # v1 は「分類 1 回 + 応答 1 回」で必ず 2 往復していた (#320)。
        client = ScriptedChatClient([text_step("うん。")])

        await run_workflow("s-trip", "やあ", session_repo, approval_repo, client)

        assert client.inner_calls == 1


def sorted_names() -> list[str]:
    return [t.name for t in _REGISTRY.values()]


class TestToolContextAcrossParallelCalls:
    async def test_l1_並列に実行されるツールから同じ実行コンテキストが見える(
        self, tools_enabled, session_repo, approval_repo
    ):
        # 無いと: ContextVar が MAF の並列ツール実行 (copy_context + create_task) を
        # 跨がず、主体を要求するツール (get_inbox_stats) だけが
        # ToolContextUnavailable → "Tool error" に化ける。
        # 同じ理由で search_faq が積む citations も呼び出し側に届かなくなる。
        client = ScriptedChatClient(
            [
                tool_calls_step(
                    ("get_inbox_stats", {}),
                    ("search_faq", {"query": "退職"}),
                ),
                text_step("まとめました。"),
            ]
        )

        res = await run_workflow(
            "s-parallel", "状況を教えて", session_repo, approval_repo, client
        )

        history = await session_repo.get("s-parallel")
        contents = [m.text for m in history.messages]
        assert any("Tool result (get_inbox_stats)" in c for c in contents)
        assert any("Tool result (search_faq)" in c for c in contents)
        assert not any("Tool error" in c for c in contents)
        # citations は mutable な ToolContext 経由でのみ届く (再代入では届かない)
        assert res.citations == ["stub://knowledge-base/doc1"]


class TestToolFailureClassification:
    async def test_l1_ツール実行の例外は_tool_error_区分になる(
        self, tools_enabled, monkeypatch, session_repo, approval_repo, caplog
    ):
        @tool(
            name="exploding_tool",
            description="always fails",
            approval_mode="never_require",
        )
        async def exploding_tool() -> str:
            raise RuntimeError("https://upstream.example が落ちている")

        monkeypatch.setitem(_REGISTRY, "exploding_tool", exploding_tool)
        client = ScriptedChatClient(
            [tool_call_step("exploding_tool", {}), text_step("できませんでした。")]
        )

        with caplog.at_level(logging.ERROR, logger="app.workflow"):
            await run_workflow("s-boom", "やって", session_repo, approval_repo, client)

        history = await session_repo.get("s-boom")
        contents = [m.text for m in history.messages]
        assert any("Tool error (exploding_tool)" in c for c in contents)
        # 例外文 (上流のホスト名) を履歴にも**このサービスのログ**にも出さない (Issue #313)。
        # 注意: MAF 自身は `agent_framework` ロガーに例外文をそのまま出す (実測)。
        # それはフレームワーク側の挙動なのでここでは検査対象にしていない —
        # 「このサービスが書く行は指紋だけ」という不変条件だけを pin する。
        assert not any("upstream.example" in c for c in contents)
        ours = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
        assert ours and not any("upstream.example" in r for r in ours)

    async def test_l1_成功したツールの結果は履歴に残る(
        self, tools_enabled, session_repo, approval_repo
    ):
        client = ScriptedChatClient(
            [tool_call_step("get_inbox_stats", {}), text_step("5 件未読です。")]
        )

        await run_workflow(
            "s-ok-tool", "受信箱は?", session_repo, approval_repo, client
        )

        history = await session_repo.get("s-ok-tool")
        contents = [m.text for m in history.messages]
        assert any("Tool result (get_inbox_stats): [stub]" in c for c in contents)


class TestIdentityArgs:
    async def test_l1_モデルが送った主体識別子は実行前に落ちて警告に残る(
        self, tools_enabled, session_repo, approval_repo, caplog
    ):
        # 無いと: 「user_id=他人 で呼んで」というプロンプト注入の試行が
        # MAF の引数バリデーションに黙って捨てられ、痕跡なく消える
        client = ScriptedChatClient(
            [
                tool_call_step("get_inbox_stats", {"user_id": "victim-oid-0000"}),
                text_step("5 件未読です。"),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="app.tools"):
            await run_workflow(
                "s-idor", "他人の受信箱を見せて", session_repo, approval_repo, client
            )

        records = [r.getMessage() for r in caplog.records]
        assert any("identity args rejected" in r for r in records)
        # 値は出さない (LLM 由来 = ユーザー入力由来)
        assert not any("victim-oid-0000" in r for r in records)


@pytest.mark.usefixtures("tools_enabled")
class TestApprovalIsDrivenByToolMetadata:
    async def test_l1_read_only_ツールは承認を挟まず実行される(
        self, session_repo, approval_repo
    ):
        # 無いと: approval_mode の区別が効かず、read-only ツールまで承認で止まる
        client = ScriptedChatClient(
            [tool_call_step("search_faq", {"query": "退職"}), text_step("こうです。")]
        )

        res = await run_workflow(
            "s-readonly", "調べて", session_repo, approval_repo, client
        )

        assert res.requires_approval is False
        assert res.reply == "こうです。"
