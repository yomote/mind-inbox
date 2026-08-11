"""[L1] MAF workflow の承認中断/再開 (checkpoint + HITL) を pin する。

無いと何が静かに通るか:
- approvalRequestId → MAF checkpoint の写像が壊れ、/approve が中断点から再開できない退行
- 承認/却下の分岐反転 (却下なのにツールが走る / 承認なのに走らない) が静かに通る
- 再開後の履歴積み忘れ (tool result / assistant 応答) で次ターンの文脈が壊れる退行

ここで test しないこと:
- SSE / HTTP の枠組み (それは L2 /chat 系)
- MAF の checkpoint 実装自体 (フレームワークの領域)
"""

import json

import pytest

from app.workflow import (
    get_checkpoint_storage,
    resume_after_approval,
    run_workflow,
)

APPROVAL_CLASSIFICATION = {
    "needs_retrieval": False,
    "needs_tool": True,
    "tool_name": "send_reply",
    "tool_args": {"to": "a@example.com", "body": "hi"},
}


class RoutedChatService:
    """classify (JSON) と respond (応答文) を呼び出し内容で振り分ける fake。"""

    CLASSIFY_MARKER = "Respond with this exact JSON structure"

    def __init__(self, classification: dict, reply: str = "対応しました。"):
        self._classification = classification
        self._reply = reply

    async def get_chat_message_content(self, chat_history, settings):
        is_classify = any(
            self.CLASSIFY_MARKER in str(m.content) for m in chat_history.messages
        )
        return json.dumps(self._classification) if is_classify else self._reply

    async def get_streaming_chat_message_content(self, chat_history, settings):
        yield self._reply


class FakeKernel:
    def __init__(self, service: RoutedChatService):
        self._service = service

    def get_service(self, name: str) -> RoutedChatService:
        return self._service


class TestApprovalCheckpointMapping:
    async def test_l1_approval_request_id_maps_to_maf_checkpoint(
        self, session_repo, approval_repo
    ):
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-map", "返信して", session_repo, approval_repo, kernel
        )

        assert res.requires_approval is True
        record = await approval_repo.get(res.approval_request_id)
        assert record is not None
        assert record.status == "pending"
        assert record.checkpoint_id is not None

        # 写像の実体: checkpoint は MAF の storage に実在し、
        # この approvalRequestId の pending request を保持している
        checkpoint = await get_checkpoint_storage().load(record.checkpoint_id)
        assert res.approval_request_id in checkpoint.pending_request_info_events


class TestResumeAfterApproval:
    async def test_l1_approved_resume_executes_tool_and_responds(
        self, session_repo, approval_repo
    ):
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-ok", "返信して", session_repo, approval_repo, kernel
        )
        reply = await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, kernel
        )

        assert reply == "対応しました。"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "approved"

        history = await session_repo.get("s-ok")
        contents = [str(m.content) for m in history.messages]
        assert any("Tool result (send_reply)" in c for c in contents)
        assert history.messages[-1].role.value == "assistant"
        assert contents[-1] == "対応しました。"

    async def test_l1_rejected_resume_cancels_without_tool(
        self, session_repo, approval_repo
    ):
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-ng", "返信して", session_repo, approval_repo, kernel
        )
        reply = await resume_after_approval(
            res.approval_request_id, False, session_repo, approval_repo, kernel
        )

        assert reply == "操作はキャンセルされました。他にご用件はありますか？"

        record = await approval_repo.get(res.approval_request_id)
        assert record.status == "rejected"

        history = await session_repo.get("s-ng")
        contents = [str(m.content) for m in history.messages]
        assert not any("Tool result" in c for c in contents)
        assert history.messages[-1].role.value == "assistant"
        assert contents[-1] == reply

    async def test_l1_resume_unknown_id_raises(self, session_repo, approval_repo):
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        with pytest.raises(ValueError, match="Approval not found"):
            await resume_after_approval(
                "no-such-id", True, session_repo, approval_repo, kernel
            )

    async def test_l1_resume_twice_raises_already_processed(
        self, session_repo, approval_repo
    ):
        kernel = FakeKernel(RoutedChatService(APPROVAL_CLASSIFICATION))

        res = await run_workflow(
            "s-twice", "返信して", session_repo, approval_repo, kernel
        )
        await resume_after_approval(
            res.approval_request_id, True, session_repo, approval_repo, kernel
        )

        with pytest.raises(ValueError, match="Approval already processed"):
            await resume_after_approval(
                res.approval_request_id, True, session_repo, approval_repo, kernel
            )
