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

import pytest
from semantic_kernel.contents import ChatHistory

from app.schemas import ChatStreamDelta, ChatStreamDone
from app.workflow import run_workflow, run_workflow_stream


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


class FailingStreamChatService(FakeChatService):
    """RESPOND のストリーミング途中で落ちる chat service (実運用の接続断を模す)。

    _classify (非ストリーミング) は分類 JSON を、_respond (非ストリーミング) は
    通常の応答文を返す — フォールバック経路をそのまま流せるようにするため、
    呼び出し内容 (分類プロンプトか否か) で返り値を切り替える。
    """

    CLASSIFY_MARKER = "Respond with this exact JSON structure"
    FALLBACK_REPLY = "取り直した応答です。"

    async def get_chat_message_content(self, chat_history, settings):
        is_classify = any(
            self.CLASSIFY_MARKER in str(m.content) for m in chat_history.messages
        )
        return json.dumps(self._classification) if is_classify else self.FALLBACK_REPLY

    async def get_streaming_chat_message_content(self, chat_history, settings):
        yield "途中まで"
        raise RuntimeError("LLM connection lost")


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

    async def test_l1_streaming_failure_then_fallback_keeps_single_user_turn(
        self, session_repo, approval_repo
    ):
        # 再現テスト (PR #132 レビュー major): ストリーミングが RESPOND 中に落ちると
        # ユーザー発言だけが保存された状態で終わる。フロントは同一 sessionId / message で
        # 非ストリーミング /chat に自動フォールバックするため、冪等化が無いと
        # 「assistant を挟まない同一 user ターンの重複」が履歴に残り、累積履歴
        # (プロダクトの核) と後続ターンの文脈解釈が壊れる。
        message = "最近よく眠れていません。"
        service = FailingStreamChatService(NO_TOOL, [])
        kernel = FakeKernel(service)

        # 1) ストリーミングが途中で失敗する
        with pytest.raises(RuntimeError, match="LLM connection lost"):
            await collect(
                run_workflow_stream(
                    "s-retry", message, session_repo, approval_repo, kernel
                )
            )

        history_after_failure: ChatHistory = await session_repo.get("s-retry")
        assert [str(m.content) for m in history_after_failure.messages].count(
            message
        ) == 1

        # 2) フロントの自動フォールバック (同一 sessionId / message で非ストリーミング)
        res = await run_workflow(
            "s-retry", message, session_repo, approval_repo, kernel
        )
        assert res.reply == FailingStreamChatService.FALLBACK_REPLY

        history: ChatHistory = await session_repo.get("s-retry")
        contents = [str(m.content) for m in history.messages]
        # ユーザー発言は 1 回だけ / 末尾は assistant 応答で閉じている
        assert contents.count(message) == 1
        assert history.messages[-1].role.value == "assistant"
        assert contents[-1] == FailingStreamChatService.FALLBACK_REPLY

    async def test_l1_retry_detected_even_when_tool_result_follows_user_message(
        self, session_repo, approval_repo
    ):
        # 無いと: read-only ツールを実行したターンが落ちた場合、履歴末尾が
        # system (Tool result) になるため「直前は user 発言」の判定が外れ、
        # フォールバックで user 発言が重複する経路だけが取り残される
        message = "受信箱の状況を教えてください。"
        classification = {
            "needs_retrieval": False,
            "needs_tool": True,
            "tool_name": "get_inbox_stats",  # read-only なので承認なしで実行される
            "tool_args": {},
        }
        kernel = FakeKernel(FailingStreamChatService(classification, []))

        with pytest.raises(RuntimeError, match="LLM connection lost"):
            await collect(
                run_workflow_stream(
                    "s-tool", message, session_repo, approval_repo, kernel
                )
            )

        # 落ちた時点の履歴末尾は system (Tool result) になっている
        failed: ChatHistory = await session_repo.get("s-tool")
        assert failed.messages[-1].role.value == "system"
        assert "Tool result" in str(failed.messages[-1].content)

        await run_workflow("s-tool", message, session_repo, approval_repo, kernel)

        history: ChatHistory = await session_repo.get("s-tool")
        assert [str(m.content) for m in history.messages].count(message) == 1

    async def test_l1_same_message_after_completed_turn_is_appended_again(
        self, session_repo, approval_repo
    ):
        # 冪等化のやり過ぎ防止: assistant 応答を挟んで同じ文面を送り直すのは
        # 正当な再発言なので、履歴に 2 回積まれなければならない
        # (無いと「同じ言葉を繰り返した」という事実が履歴から消える)
        message = "やっぱり不安です。"
        kernel = FakeKernel(FakeChatService(NO_TOOL, ["わかりました。"]))

        await collect(
            run_workflow_stream("s-dup", message, session_repo, approval_repo, kernel)
        )
        await collect(
            run_workflow_stream("s-dup", message, session_repo, approval_repo, kernel)
        )

        history: ChatHistory = await session_repo.get("s-dup")
        assert [str(m.content) for m in history.messages].count(message) == 2

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
