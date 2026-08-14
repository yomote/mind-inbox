"""MAF の層を**本物のまま**通す台本式 chat client fake (#320)。

なぜ「MagicMock で get_response を差し替える」ではダメか:
ツール選択・引数検証・承認 (`approval_mode`)・実行はすべて MAF の
`FunctionInvocationLayer` が chat client の内側でやる。get_response 丸ごとを
差し替えた fake だと**その層が 1 行も動かない**ので、承認の分岐や引数検証を
テストしているつもりで実際にはテストの中の if 文を見ているだけになる。

そこで OpenAIChatClient と同じ層構成を継承し、**一番内側 (`_inner_get_response`
= HTTP を叩く場所) だけ**を台本に差し替える。台本の 1 要素 = モデルの 1 往復。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)
from agent_framework._tools import FunctionInvocationLayer
from agent_framework.observability import ChatTelemetryLayer


@dataclass
class Step:
    """モデルの 1 往復ぶんの返答。"""

    contents: list[Any]
    # ストリーミング時の分割。None なら contents を 1 update でまとめて流す
    chunks: Optional[list[Content]] = None
    # この数の chunk を流した後に例外を投げる (接続断の再現)。None なら投げない
    fail_after: Optional[int] = None
    # 呼ばれた瞬間に例外を投げる (stream / 非 stream の両方)。fail_after は
    # ストリーミングでしか効かないので、非 stream の失敗はこちらで作る
    fail_immediately: bool = False
    error: str = "LLM connection lost"

    def as_message(self) -> Message:
        return Message(role="assistant", contents=list(self.contents))


def text_step(
    *chunks: str, fail_after: Optional[int] = None, error: str = "LLM connection lost"
) -> Step:
    """モデルが応答テキストを返す 1 往復 (chunks はストリーミング時の分割)。"""
    return Step(
        contents=[Content.from_text(chunk) for chunk in chunks],
        chunks=[Content.from_text(chunk) for chunk in chunks],
        fail_after=fail_after,
        error=error,
    )


def error_step(error: str = "LLM connection lost") -> Step:
    """モデル呼び出しそのものが落ちる 1 往復 (非 stream でも投げる)。

    承認再開は常に非 stream なので、再開中の LLM 障害はこれでしか作れない。
    """
    return Step(contents=[], fail_immediately=True, error=error)


def tool_call_step(name: str, arguments: dict, *, call_id: str = "call-1") -> Step:
    """モデルがツール呼び出しを返す 1 往復。"""
    return Step(
        contents=[
            Content.from_function_call(call_id=call_id, name=name, arguments=arguments)
        ]
    )


def tool_calls_step(*calls: tuple[str, dict]) -> Step:
    """モデルが同一往復で複数ツールを並列に呼ぶ 1 往復。"""
    return Step(
        contents=[
            Content.from_function_call(
                call_id=f"call-{index}", name=name, arguments=arguments
            )
            for index, (name, arguments) in enumerate(calls)
        ]
    )


@dataclass
class _Recorded:
    options: dict[str, Any] = field(default_factory=dict)


class ScriptedChatClient(
    FunctionInvocationLayer, ChatMiddlewareLayer, ChatTelemetryLayer, BaseChatClient
):
    """台本どおりに返す chat client。MAF の function invocation loop は本物が回る。

    - `steps`: モデルの往復ごとの返答。使い切ったら `default_reply` を返す。
    - `inner_calls`: `_inner_get_response` が呼ばれた回数 = **LLM 往復の実測値**。
    - `seen_options`: 各往復で渡された options (`tools` の中身の検証に使う)。
    """

    OTEL_PROVIDER_NAME = "fake"

    def __init__(
        self,
        steps: Sequence[Step] = (),
        *,
        default_reply: str = "対応しました。",
    ) -> None:
        super().__init__()
        self._steps = list(steps)
        self._default_reply = default_reply
        self.inner_calls = 0
        self.seen_options: list[dict[str, Any]] = []

    @property
    def tool_names_seen(self) -> list[list[str]]:
        """各往復で LLM に渡されたツール名 (registry → options の配線を見る)。"""
        return [
            [getattr(t, "name", str(t)) for t in (opts.get("tools") or [])]
            for opts in self.seen_options
        ]

    def _next_step(self) -> Step:
        if self._steps:
            return self._steps.pop(0)
        return text_step(self._default_reply)

    def _inner_get_response(self, *, messages, stream, options=None, **kwargs):
        self.inner_calls += 1
        self.seen_options.append(dict(options or {}))
        step = self._next_step()

        if not stream:

            async def _respond() -> ChatResponse:
                if step.fail_immediately:
                    raise RuntimeError(step.error)
                return ChatResponse(messages=[step.as_message()], response_id="fake")

            return _respond()

        async def _emit():
            if step.fail_immediately:
                raise RuntimeError(step.error)
            if step.chunks is None:
                # ツール呼び出し等、テキストでない content はまとめて 1 update で流す
                yield ChatResponseUpdate(role="assistant", contents=step.contents)
                return
            for index, chunk in enumerate(step.chunks):
                if step.fail_after is not None and index >= step.fail_after:
                    raise RuntimeError(step.error)
                yield ChatResponseUpdate(role="assistant", contents=[chunk])
            if step.fail_after is not None:
                raise RuntimeError(step.error)

        return ResponseStream(_emit(), finalizer=ChatResponse.from_updates)
