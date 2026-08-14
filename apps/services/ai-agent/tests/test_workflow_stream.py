"""[L1] run_workflow_stream の分岐テスト (MAF の層を通す台本 fake に差し替え)。

無いと何が静かに通るか:
- ストリーミング経路だけ履歴保存 (assistant メッセージ追記) を忘れても /chat 系 L2 は
  非ストリーミング経路しか見ないため、次ターンで文脈が消える退行が静かに通る
- 承認が要るターンで delta を流してしまう / done に承認情報が乗らない退行が静かに通る
- 応答生成が落ちたターンで、**実行済みツールの事実が履歴から消える**退行
  (フォールバックで同じツールがもう一度呼ばれる)

ここで test しないこと:
- SSE の HTTP 枠組み (それは L2 /chat/stream)
- LLM 出力品質 / MAF の streaming API 自体

fixture 置き換え (#320): 自前分類プロンプトを廃し MAF ネイティブの function calling に
載せたので、fake は `tests/fakes.ScriptedChatClient` (MAF の層は本物) に置き換えた。
各テストのアサーション (履歴保存 / 冪等化 / 承認分岐) の検証意図は不変。
"""

import pytest

from app.history import ChatHistory
from app.schemas import ChatStreamDelta, ChatStreamDone
from app.workflow import run_workflow, run_workflow_stream
from tests.fakes import ScriptedChatClient, text_step, tool_call_step

pytestmark = pytest.mark.usefixtures("tools_enabled")

FALLBACK_REPLY = "取り直した応答です。"


async def collect(gen):
    return [event async for event in gen]


class TestRunWorkflowStream:
    async def test_l1_streams_deltas_then_done_and_saves_history(
        self, session_repo, approval_repo
    ):
        client = ScriptedChatClient([text_step("それは", "大変でし", "たね。")])

        events = await collect(
            run_workflow_stream("s1", "疲れました", session_repo, approval_repo, client)
        )

        deltas = [e for e in events if isinstance(e, ChatStreamDelta)]
        assert [d.text for d in deltas] == ["それは", "大変でし", "たね。"]

        done = events[-1]
        assert isinstance(done, ChatStreamDone)
        assert done.response.reply == "それは大変でしたね。"
        assert done.response.requires_approval is False

        # ストリーミング経路でも履歴に user + assistant が積まれている
        history: ChatHistory = await session_repo.get("s1")
        roles = [m.role for m in history.messages]
        assert roles[-2:] == ["user", "assistant"]
        assert history.messages[-1].text == "それは大変でしたね。"

    async def test_l1_ツールを使わない通常ターンの_llm_往復は_1_回(
        self, session_repo, approval_repo
    ):
        # 無いと: 自前の分類プロンプト (CLASSIFY) が復活しても気づけない。
        # v1 は「分類 1 回 + 応答 1 回」で必ず 2 往復していた (#320)。
        client = ScriptedChatClient([text_step("うん、うん。")])

        await collect(
            run_workflow_stream(
                "s-1trip", "聞いて", session_repo, approval_repo, client
            )
        )

        assert client.inner_calls == 1

    async def test_l1_streaming_failure_then_fallback_keeps_single_user_turn(
        self, session_repo, approval_repo
    ):
        # 再現テスト (PR #132 レビュー major): ストリーミングが応答生成中に落ちると
        # ユーザー発言だけが保存された状態で終わる。フロントは同一 sessionId / message で
        # 非ストリーミング /chat に自動フォールバックするため、冪等化が無いと
        # 「assistant を挟まない同一 user ターンの重複」が履歴に残り、累積履歴
        # (プロダクトの核) と後続ターンの文脈解釈が壊れる。
        message = "最近よく眠れていません。"
        client = ScriptedChatClient(
            [text_step("途中まで", fail_after=1), text_step(FALLBACK_REPLY)]
        )

        # 1) ストリーミングが途中で失敗する
        with pytest.raises(RuntimeError, match="LLM connection lost"):
            await collect(
                run_workflow_stream(
                    "s-retry", message, session_repo, approval_repo, client
                )
            )

        history_after_failure: ChatHistory = await session_repo.get("s-retry")
        assert [m.text for m in history_after_failure.messages].count(message) == 1

        # 2) フロントの自動フォールバック (同一 sessionId / message で非ストリーミング)
        res = await run_workflow(
            "s-retry", message, session_repo, approval_repo, client
        )
        assert res.reply == FALLBACK_REPLY

        history: ChatHistory = await session_repo.get("s-retry")
        contents = [m.text for m in history.messages]
        # ユーザー発言は 1 回だけ / 末尾は assistant 応答で閉じている
        assert contents.count(message) == 1
        assert history.messages[-1].role == "assistant"
        assert contents[-1] == FALLBACK_REPLY

    async def test_l1_retry_detected_even_when_tool_result_follows_user_message(
        self, session_repo, approval_repo
    ):
        # 無いと: read-only ツールを実行したターンが落ちた場合、履歴末尾が
        # system (Tool result) になるため「直前は user 発言」の判定が外れ、
        # フォールバックで user 発言が重複する経路だけが取り残される
        message = "受信箱の状況を教えてください。"
        client = ScriptedChatClient(
            [
                # read-only ツールなので承認なしで MAF が実行する
                tool_call_step("get_inbox_stats", {}),
                text_step("途中まで", fail_after=1),
                text_step(FALLBACK_REPLY),
            ]
        )

        with pytest.raises(RuntimeError, match="LLM connection lost"):
            await collect(
                run_workflow_stream(
                    "s-tool", message, session_repo, approval_repo, client
                )
            )

        # 落ちた時点の履歴末尾は system (Tool result) — ツールは実際に走ったので、
        # 応答生成が落ちてもその事実は履歴に残す
        failed: ChatHistory = await session_repo.get("s-tool")
        assert failed.messages[-1].role == "system"
        assert "Tool result" in failed.messages[-1].text

        await run_workflow("s-tool", message, session_repo, approval_repo, client)

        history: ChatHistory = await session_repo.get("s-tool")
        assert [m.text for m in history.messages].count(message) == 1

    async def test_l1_same_message_after_completed_turn_is_appended_again(
        self, session_repo, approval_repo
    ):
        # 冪等化のやり過ぎ防止: assistant 応答を挟んで同じ文面を送り直すのは
        # 正当な再発言なので、履歴に 2 回積まれなければならない
        # (無いと「同じ言葉を繰り返した」という事実が履歴から消える)
        message = "やっぱり不安です。"
        client = ScriptedChatClient(
            [text_step("わかりました。"), text_step("わかりました。")]
        )

        await collect(
            run_workflow_stream("s-dup", message, session_repo, approval_repo, client)
        )
        await collect(
            run_workflow_stream("s-dup", message, session_repo, approval_repo, client)
        )

        history: ChatHistory = await session_repo.get("s-dup")
        assert [m.text for m in history.messages].count(message) == 2

    async def test_l1_side_effecting_tool_yields_done_only_with_approval(
        self, session_repo, approval_repo
    ):
        client = ScriptedChatClient(
            [tool_call_step("send_reply", {"to": "a@example.com", "body": "hi"})]
        )

        events = await collect(
            run_workflow_stream("s1", "返信して", session_repo, approval_repo, client)
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
