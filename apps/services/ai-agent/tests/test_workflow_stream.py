"""[L1] run_workflow_stream の分岐テスト (kernel の chat service を fake に差し替え)。

無いと何が静かに通るか:
- ストリーミング経路だけ履歴保存 (assistant メッセージ追記) を忘れても /chat 系 L2 は
  非ストリーミング経路しか見ないため、次ターンで文脈が消える退行が静かに通る
- 承認が要るターンで delta を流してしまう / done に approval 情報が乗らない退行が静かに通る

ここで test しないこと:
- SSE の HTTP 枠組み (それは L2 /chat/stream)
- LLM 出力品質 / SK の streaming API 自体
"""

import json

from semantic_kernel.contents import ChatHistory

from app.schemas import ChatStreamDelta, ChatStreamDone
from app.workflow import run_workflow_stream


class FakeChatService:
    """_classify (非ストリーミング) と _respond_stream (ストリーミング) の両方に応える。"""

    def __init__(self, classification: dict, stream_chunks: list[str]):
        self._classification = classification
        self._stream_chunks = stream_chunks

    async def get_chat_message_content(self, chat_history, settings):
        return json.dumps(self._classification)

    async def get_streaming_chat_message_content(self, chat_history, settings):
        for chunk in self._stream_chunks:
            yield chunk


class FakeKernel:
    def __init__(self, service: FakeChatService):
        self._service = service

    def get_service(self, name: str) -> FakeChatService:
        return self._service


NO_TOOL = {
    "needs_retrieval": False,
    "needs_tool": False,
    "tool_name": None,
    "tool_args": {},
}


async def collect(gen):
    return [event async for event in gen]


class TestRunWorkflowStream:
    async def test_l1_streams_deltas_then_done_and_saves_history(
        self, session_repo, approval_repo
    ):
        kernel = FakeKernel(FakeChatService(NO_TOOL, ["それは", "大変でし", "たね。"]))

        events = await collect(
            run_workflow_stream("s1", "疲れました", session_repo, approval_repo, kernel)
        )

        deltas = [e for e in events if isinstance(e, ChatStreamDelta)]
        assert [d.text for d in deltas] == ["それは", "大変でし", "たね。"]

        done = events[-1]
        assert isinstance(done, ChatStreamDone)
        assert done.response.reply == "それは大変でしたね。"
        assert done.response.requires_approval is False

        # ストリーミング経路でも履歴に user + assistant が積まれている
        history: ChatHistory = await session_repo.get("s1")
        roles = [m.role.value for m in history.messages]
        assert roles[-2:] == ["user", "assistant"]
        assert str(history.messages[-1].content) == "それは大変でしたね。"

    async def test_l1_side_effecting_tool_yields_done_only_with_approval(
        self, session_repo, approval_repo
    ):
        classification = {
            "needs_retrieval": False,
            "needs_tool": True,
            "tool_name": "send_reply",
            "tool_args": {"to": "a@example.com", "body": "hi"},
        }
        kernel = FakeKernel(FakeChatService(classification, ["流れてはいけない"]))

        events = await collect(
            run_workflow_stream("s1", "返信して", session_repo, approval_repo, kernel)
        )

        # 承認待ちターンは delta を流さない (承認文言は逐次配信の対象外)
        assert len(events) == 1
        done = events[0]
        assert isinstance(done, ChatStreamDone)
        assert done.response.requires_approval is True
        assert done.response.approval_request_id is not None

        record = await approval_repo.get(done.response.approval_request_id)
        assert record is not None
        assert record.plan.tool_name == "send_reply"
