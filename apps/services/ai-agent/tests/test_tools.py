"""[L1] tool registry — read-only / side-effecting 区分のメタデータを pin する (M1-4 / #82)。

無いと何が静かに通るか:
- @ai_function (@tool) 化で approval_mode の付け間違い / 付け忘れが静かに通り、
  副作用ツール (send_reply / archive_message) が**承認なしで実行される** (G1 崩壊)、
  または read-only ツールまで承認要求で止まる退行
- 未知ツール名の実行が例外にならず素通りする退行 (workflow の "Tool error" 経路が死ぬ)

ここで test しないこと:
- ツールの中身 (M3-5 で実体化するまでスタブ)
- 承認フローの通し挙動 (test_workflow_approval.py が pin 済み)
"""

import pytest

from app.tools import _REGISTRY, execute_tool, is_side_effecting


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
        result = await execute_tool("get_inbox_stats", {})
        assert isinstance(result, str)
        assert "[stub]" in result

    async def test_l1_unknown_tool_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            await execute_tool("no-such-tool", {})
