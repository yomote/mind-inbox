"""[L1] tool registry — 承認要否のメタデータと、LLM に見せるツールの導出を pin する (#320 / #82)。

無いと何が静かに通るか:
- @ai_function (@tool) 化で approval_mode の付け間違い / 付け忘れが静かに通り、
  副作用ツール (send_reply / archive_message) が**承認なしで実行される** (G1 崩壊)、
  または read-only ツールまで承認要求で止まる退行
- registry に足したツールが LLM に渡らない / 逆に既定オフのはずのツールが
  勝手に見えている退行 (#82 design-gate の PO 裁定 2)
- ツール引数から主体 (user_id 等) を受け取る設計の復活 (IDOR / Issue #313)

Issue #313 で「ツール権限の不変条件」を機械検査する `TestToolPermissionInvariants`
を追加した (LLM を採点者にしない決定的テスト / 判定は「機構が何をしたか」に寄せる)。

ここで test しないこと:
- ツールの中身 (M3-5 で実体化するまでスタブ)
- 承認フローの通し挙動 (test_workflow_approval.py が pin 済み)
- MAF の loop に載せた配線 (test_workflow_tools.py が実ループを通して pin 済み)
"""

import pytest
from agent_framework import Content, tool

from app.tools import (
    _REGISTRY,
    IDENTITY_ARG_NAMES,
    ToolContext,
    ToolContextUnavailable,
    UnknownExposedTool,
    exposed_tools,
    function_invocation_limits,
    get_inbox_stats,
    is_side_effecting,
    report_identity_arg_attempts,
    use_tool_context,
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
        assert is_side_effecting("no-such-tool") is False
        assert is_side_effecting(None) is False

    def test_l1_every_registered_tool_declares_approval_mode(self):
        # 登録ツールを増やしたとき approval_mode 未指定 (None) だと、
        # MAF の invocation loop が承認要求を出さず**そのまま実行**してしまう
        for name, entry in _REGISTRY.items():
            assert entry.approval_mode in ("never_require", "always_require"), name


# ── LLM に見せるツールの導出 (#320 / PO 裁定 2) ───────────────────────────────


class TestExposedTools:
    def test_l1_既定では_llm_に_1_本も見せない(self, monkeypatch):
        # 無いと: #321 (題材の再定義) の裁定前に、SK 時代のスタブ題材が
        # 実運用の会話へ出る
        from app.config import get_settings

        monkeypatch.delenv("LLM_EXPOSED_TOOLS", raising=False)
        get_settings.cache_clear()
        try:
            assert exposed_tools() == ()
        finally:
            get_settings.cache_clear()

    def test_l1_registry_に足したツールは_見せる設定なら自動で増える(
        self, tools_enabled, monkeypatch
    ):
        # 無いと: LLM に渡すツール一覧が registry とは別の場所に書かれ、
        # 足しても LLM からは一生呼ばれない二重管理に戻る (#320 の中核)
        @tool(name="probe_tool", description="probe", approval_mode="never_require")
        async def probe_tool(x: str) -> str:  # pragma: no cover - 呼ばれない
            return x

        before = {t.name for t in exposed_tools()}
        monkeypatch.setitem(_REGISTRY, "probe_tool", probe_tool)

        after = {t.name for t in exposed_tools()}
        assert after == before | {"probe_tool"}

    def test_l1_名前を列挙すればその分だけ見せる(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("LLM_EXPOSED_TOOLS", "search_faq, get_inbox_stats")
        get_settings.cache_clear()
        try:
            assert [t.name for t in exposed_tools()] == [
                "search_faq",
                "get_inbox_stats",
            ]
        finally:
            get_settings.cache_clear()

    def test_l1_未知のツール名は握り潰さず失敗する(self, monkeypatch):
        # 無いと: 綴り間違いが「フラグを立てたのに何も起きない」に化け、
        # 正常系 (既定オフ) と区別できない
        from app.config import get_settings

        monkeypatch.setenv("LLM_EXPOSED_TOOLS", "send_reply,no_such_tool")
        get_settings.cache_clear()
        try:
            with pytest.raises(UnknownExposedTool, match="no_such_tool"):
                exposed_tools()
        finally:
            get_settings.cache_clear()


class TestFunctionInvocationLimits:
    def test_l1_ツール実行の上限が明示されている(self):
        # 無いと: MAF の既定 (max_function_calls=None = 無制限) のまま本番に出て、
        # 暴走したモデルがトークンと外向き I/O を止めどなく使う
        limits = function_invocation_limits()
        assert limits["max_function_calls"] >= 1
        assert limits["max_iterations"] >= 1


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
    # 選択肢の提示 (#432-b)。**副作用は無い** — 会話の分岐を差し出すだけで、
    # 状態は何も変えないし、押されなくてもターンは完結する (完了型 / PO 裁定)
    "offer_choices": "never_require",
    "send_reply": "always_require",
    "archive_message": "always_require",
}


class TestToolPermissionInvariants:
    def test_単体_登録ツールの承認要否は宣言表と一致する(self):
        # 無いと: 新しいツールが承認要否を決めないまま registry に載り、
        # approval_mode 未指定 → MAF が承認要求を出さず **承認なしで実行**される
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

    def test_単体_モデルが送った主体識別子は痕跡を残して弾かれる(self):
        # 無いと: MAF の引数バリデーションが未宣言の引数を黙って捨てるので、
        # プロンプト注入の**試行が痕跡なく消える** (攻撃に気づけない)
        calls = [
            Content.from_function_call(
                call_id="c1",
                name="get_inbox_stats",
                arguments={"user_id": "victim-oid-0000"},
            ),
            Content.from_function_call(
                call_id="c2", name="search_faq", arguments={"query": "退職"}
            ),
        ]

        assert report_identity_arg_attempts(calls) == ["get_inbox_stats"]

    async def test_単体_実行コンテキスト無しでは主体依存ツールを実行できない(self):
        # 無いと: 主体が「既定値」から来る設計 (旧 user_id="default") に戻り、
        # 誰として実行しているのか分からないまま実体化できてしまう
        with pytest.raises(ToolContextUnavailable):
            await get_inbox_stats()

    async def test_単体_実行コンテキストのスコープを抜けたら見えなくなる(self):
        # 無いと: ContextVar を立てっぱなしにする実装に戻り、
        # 別のリクエストのツールが前のリクエストの主体で走りうる
        with use_tool_context(CTX):
            assert await get_inbox_stats()
        with pytest.raises(ToolContextUnavailable):
            await get_inbox_stats()
