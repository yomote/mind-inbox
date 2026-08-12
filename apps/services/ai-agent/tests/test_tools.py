"""[L1] tool registry — read-only / side-effecting 区分のメタデータを pin する (M1-4 / #82)。

無いと何が静かに通るか:
- @ai_function (@tool) 化で approval_mode の付け間違い / 付け忘れが静かに通り、
  副作用ツール (send_reply / archive_message) が**承認なしで実行される** (G1 崩壊)、
  または read-only ツールまで承認要求で止まる退行
- 未知ツール名の実行が例外にならず素通りする退行 (workflow の "Tool error" 経路が死ぬ)

Issue #313 で「ツール権限の不変条件」を機械検査する `TestToolPermissionInvariants`
を追加した (LLM を採点者にしない決定的テスト / 判定は「機構が何をしたか」に寄せる)。

ここで test しないこと:
- ツールの中身 (M3-5 で実体化するまでスタブ)
- 承認フローの通し挙動 (test_workflow_approval.py が pin 済み)
"""

import pytest

from app.tools import (
    _REGISTRY,
    IDENTITY_ARG_NAMES,
    ToolContext,
    ToolContextUnavailable,
    execute_tool,
    get_inbox_stats,
    is_side_effecting,
)

CTX = ToolContext(session_id="s-test")


class TestSideEffectMetadata:
    def test_l1_read_only_tools_do_not_require_approval(self):
        assert is_side_effecting("search_faq") is False
        assert is_side_effecting("get_inbox_stats") is False

    def test_l1_side_effecting_tools_require_approval(self):
        assert is_side_effecting("send_reply") is True
        assert is_side_effecting("archive_message") is True

    def test_l1_unknown_or_missing_tool_is_not_side_effecting(self):
        # v1 と同一: 未知 / None は False (workflow の needs_tool 判定に委ねる)
        assert is_side_effecting("no-such-tool") is False
        assert is_side_effecting(None) is False

    def test_l1_every_registered_tool_declares_approval_mode(self):
        # 登録ツールを増やしたとき approval_mode 未指定 (None) だと、
        # is_side_effecting が False に倒れて副作用ツールが承認を素通りする
        for name, entry in _REGISTRY.items():
            assert entry.approval_mode in ("never_require", "always_require"), name


class TestExecuteTool:
    async def test_l1_executes_registered_tool_and_returns_str(self):
        result = await execute_tool("get_inbox_stats", {}, CTX)
        assert isinstance(result, str)
        assert "[stub]" in result

    async def test_l1_unknown_tool_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            await execute_tool("no-such-tool", {}, CTX)


# ── ツール権限の不変条件 (Issue #313) ─────────────────────────────────────────
#
# CLAUDE.md の不変条件「副作用ツールは requiresApproval で人間に返す」と、
# 「主体はモデル出力から取らない」を **registry 全体に対して**機械で検査する。
# 個別ツールの例ではなく registry を走査する形にしてあるのは、**将来ツールを
# 追加した人が承認フラグを付け忘れたら落ちる**ようにするため。

# 承認要否の宣言表 (= 人間が一度判断した記録)。ツールを足したらここにも足す。
# 足さないと下の 1 本目が落ちる = 「承認要否を決めないまま registry に載せた」を止める。
DECLARED_APPROVAL_MODE = {
    "search_faq": "never_require",
    "get_inbox_stats": "never_require",
    "send_reply": "always_require",
    "archive_message": "always_require",
}


class TestToolPermissionInvariants:
    def test_単体_登録ツールの承認要否は宣言表と一致する(self):
        # 無いと: 新しいツールが承認要否を決めないまま registry に載り、
        # approval_mode 未指定 → is_side_effecting=False → **承認なしで実行**される
        assert set(_REGISTRY) == set(DECLARED_APPROVAL_MODE)
        for name, entry in _REGISTRY.items():
            assert entry.approval_mode == DECLARED_APPROVAL_MODE[name], name

    def test_単体_is_side_effecting_は_always_require_とだけ一致する(self):
        # 無いと: 判定関数がメタデータから乖離しても (別のフラグを見る等) 気づけず、
        # 「メタデータ上は要承認なのに実行だけ素通り」がありうる
        for name, entry in _REGISTRY.items():
            assert is_side_effecting(name) is (
                entry.approval_mode == "always_require"
            ), name

    def test_単体_モデルが主体識別子を指定できるツールは無い(self):
        # 無いと: user_id 等をツール引数 (= LLM の出力) で受け取る設計が復活し、
        # 「user_id=他人 で呼んで」というプロンプト 1 通で IDOR が成立する
        for name, entry in _REGISTRY.items():
            declared = set(entry.parameters().get("properties", {}))
            leaked = {p for p in declared if p.lower() in IDENTITY_ARG_NAMES}
            assert not leaked, f"{name} がモデル出力から主体を受け取っている: {leaked}"

    async def test_単体_モデルが送った主体識別子は実行前に捨てられる(self):
        # 無いと: ツール側が受け取らなくても「引数エラーで落ちた」に化けるだけで、
        # 注入の試行が握り潰される (二重防御が効いているか自体が見えなくなる)
        result = await execute_tool(
            "get_inbox_stats", {"user_id": "victim-oid-0000"}, CTX
        )
        assert "[stub]" in result

    async def test_単体_実行コンテキスト無しでは主体依存ツールを実行できない(self):
        # 無いと: 主体が「既定値」から来る設計 (旧 user_id="default") に戻り、
        # 誰として実行しているのか分からないまま実体化できてしまう
        with pytest.raises(ToolContextUnavailable):
            await get_inbox_stats()
