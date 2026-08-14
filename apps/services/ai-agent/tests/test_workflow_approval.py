"""[L1] MAF workflow の承認中断/再開 (checkpoint + HITL) を pin する。

無いと何が静かに通るか:
- approvalRequestId → MAF checkpoint の写像が壊れ、/approve が中断点から再開できない退行
- 承認/却下の分岐反転 (却下なのにツールが走る / 承認なのに走らない) が静かに通る
- 再開後の履歴積み忘れ (tool result / assistant 応答) で次ターンの文脈が壊れる退行
- 完了した run の checkpoint が解放されず、長寿命レプリカのメモリを食い潰す退行
  (PR #243 レビュー指摘)

ここで test しないこと:
- SSE / HTTP の枠組み (それは L2 /chat 系)
- MAF の checkpoint 実装自体 (フレームワークの領域)

fixture 置き換え (#320): 承認は自前の `is_side_effecting` 判定ではなく
`@tool(approval_mode="always_require")` を読む **MAF の function invocation loop** が
起こすようになった。したがって fake は「get_response をまるごと差し替えたもの」では
意味がなく、MAF の層を本物のまま通す `tests/fakes.ScriptedChatClient` を使う。
検証意図 (承認の中断 / 再開 / 却下 / 掃除) は不変。
"""

import pytest

from app.workflow import (
    _pending_run_storages,
    get_pending_checkpoint_storage,
    resume_after_approval,
    run_workflow,
)
from tests.fakes import ScriptedChatClient, text_step, tool_call_step

pytestmark = pytest.mark.usefixtures("tools_enabled")

APPROVAL_ARGS = {"to": "a@example.com", "body": "hi"}


def approval_script(reply: str = "対応しました。") -> ScriptedChatClient:
    """副作用ツールを呼ぶ → (承認後) 応答テキスト、の 2 往復ぶんの台本。"""
    return ScriptedChatClient(
        [tool_call_step("send_reply", APPROVAL_ARGS), text_step(reply)]
    )


class TestApprovalCheckpointMapping:
    async def test_l1_approval_request_id_maps_to_maf_checkpoint(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-map", "返信して", session_repo, approval_repo, client
        )

        assert res.requires_approval is True
        record = await approval_repo.get(res.approval_request_id)
        assert record is not None
        assert record.status == "pending"
        assert record.checkpoint_id is not None

        # 写像の実体: checkpoint はこの run の storage に実在し、
        # この approvalRequestId の pending request を保持している
        storage = get_pending_checkpoint_storage(res.approval_request_id)
        assert storage is not None
        checkpoint = await storage.load(record.checkpoint_id)
        assert res.approval_request_id in checkpoint.pending_request_info_events

    async def test_l1_approval_plan_carries_model_supplied_tool_call(
        self, session_repo, approval_repo
    ):
        # 無いと: 承認 UI に「何を実行しようとしているか」が渡らず、
        # 中身を見ずに承認させる画面になる (G1 が形骸化する)
        client = approval_script()

        res = await run_workflow(
            "s-plan", "返信して", session_repo, approval_repo, client
        )

        record = await approval_repo.get(res.approval_request_id)
        assert record.plan.tool_name == "send_reply"
        assert record.plan.tool_args == APPROVAL_ARGS
        assert record.plan.is_side_effecting is True


class TestResumeAfterApproval:
    async def test_l1_approved_resume_executes_tool_and_responds(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-ok", "返信して", session_repo, approval_repo, client
        )
        reply = await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        assert reply == "対応しました。"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "approved"

        history = await session_repo.get("s-ok")
        contents = [m.text for m in history.messages]
        assert any("Tool result (send_reply)" in c for c in contents)
        assert history.messages[-1].role == "assistant"
        assert contents[-1] == "対応しました。"

    async def test_l1_rejected_resume_cancels_without_tool(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-ng", "返信して", session_repo, approval_repo, client
        )
        reply = await resume_after_approval(
            res.approval_request_id, False, session_repo, approval_repo, client
        )

        assert reply == "操作はキャンセルされました。他にご用件はありますか？"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "rejected"

        history = await session_repo.get("s-ng")
        contents = [m.text for m in history.messages]
        assert not any("Tool result" in c for c in contents)
        assert history.messages[-1].role == "assistant"
        assert contents[-1] == reply

    async def test_l1_rejection_costs_no_llm_roundtrip(
        self, session_repo, approval_repo
    ):
        # 無いと: 却下でも応答生成のために LLM を叩く実装に戻り、
        # 「キャンセルしただけなのにトークンを燃やす」に静かに退行する
        client = approval_script()

        res = await run_workflow(
            "s-ng-cost", "返信して", session_repo, approval_repo, client
        )
        calls_before = client.inner_calls
        await resume_after_approval(
            res.approval_request_id, False, session_repo, approval_repo, client
        )

        assert client.inner_calls == calls_before

    async def test_l1_resolved_approval_releases_checkpoint_storage(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-release", "返信して", session_repo, approval_repo, client
        )
        assert get_pending_checkpoint_storage(res.approval_request_id) is not None

        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        assert get_pending_checkpoint_storage(res.approval_request_id) is None

    async def test_l1_resume_unknown_id_raises(self, session_repo, approval_repo):
        client = approval_script()

        with pytest.raises(ValueError, match="Approval not found"):
            await resume_after_approval(
                "no-such-id", True, session_repo, approval_repo, client
            )

    async def test_l1_resume_twice_raises_already_processed(
        self, session_repo, approval_repo
    ):
        client = approval_script()

        res = await run_workflow(
            "s-twice", "返信して", session_repo, approval_repo, client
        )
        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, client
        )

        with pytest.raises(ValueError, match="Approval already processed"):
            await resume_after_approval(
                res.approval_request_id, True, session_repo, approval_repo, client
            )


class TestCheckpointCleanup:
    async def test_l1_completed_run_without_approval_retains_no_checkpoints(
        self, session_repo, approval_repo
    ):
        client = ScriptedChatClient([text_step("こんにちは。")])
        before = dict(_pending_run_storages)

        res = await run_workflow(
            "s-clean", "こんにちは", session_repo, approval_repo, client
        )

        assert res.requires_approval is False
        # 承認と無関係な run はプロセス側に checkpoint 参照を一切残さない
        assert _pending_run_storages == before
