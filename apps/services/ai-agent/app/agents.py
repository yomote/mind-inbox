"""
Microsoft Agent Framework の chat client シングルトン (ADR 0016, M1)。

extractor / planner (単発 structured 呼び出し系 = `complete`) と
workflow.py (/chat 系 = `chat` / `chat_stream`) が共有する、
このサービス唯一の LLM 呼び出し面 (M1-5 で SK 依存を除去し一本化)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    FunctionTool,
    Message,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions

from .config import get_settings
from .tools import function_invocation_limits, identity_arg_guard, tool_boundary

logger = logging.getLogger(__name__)

_client: BaseChatClient | None = None

# temperature / max_tokens を受け付けない推論モデルの接頭辞 (#55)。
# Azure ではデプロイ名がモデル名と一致する運用 (gpt-4o / gpt-5-mini 等) を前提とする。
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# ツール実行に必ず通す function middleware。**順序が意味を持つ**: 外側の
# `tool_boundary` が例外の一般化 (#417 P2 / Issue #418) と実行の記録 (#417 P1) を
# 担うので、内側 (identity_arg_guard やツール本体) で起きた例外もここで捕まる。
_TOOL_MIDDLEWARE = [tool_boundary, identity_arg_guard]


def _options_for_model(
    model: str | None, tools: Sequence[FunctionTool] = ()
) -> OpenAIChatOptions:
    """モデル名に応じた既定オプション。推論モデルは temperature / max_tokens 非対応のため付けない。

    `tools` が空でなければ MAF ネイティブの function calling を有効にする (#320)。
    渡す中身は tools.py の registry から導出されたものだけ — ここでツール一覧を
    書き足す余地を作らない (真実源を 1 つに保つ)。
    """
    if model and model.lower().startswith(_REASONING_MODEL_PREFIXES):
        options = OpenAIChatOptions()
    else:
        options = OpenAIChatOptions(temperature=0.7, max_tokens=1024)
    if tools:
        options["tools"] = list(tools)
    return options


def _apply_function_invocation_limits(client: BaseChatClient) -> None:
    """ツール実行の上限を chat client に載せる (MAF の既定は max_function_calls=None = 無制限)。

    per-call のオプションでは渡せないので client 側の設定を書き換える。冪等なので
    毎回呼んでよい。**属性が無い client (= function invocation loop を持たない実装) は
    黙って素通りさせない** — 上限が効いていないことに気づけなくなるため警告を残す。
    """
    configuration = getattr(client, "function_invocation_configuration", None)
    if configuration is None:
        logger.warning(
            "Chat client (%s) has no function_invocation_configuration — "
            "ツール実行の上限が効きません",
            type(client).__name__,
        )
        return
    configuration.update(function_invocation_limits())


def _current_model() -> str | None:
    settings = get_settings()
    if settings.azure_openai_endpoint:
        return settings.azure_openai_deployment
    return settings.openai_model


def get_chat_client() -> BaseChatClient:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _with_http_timeout(client: BaseChatClient, timeout: float) -> BaseChatClient:
    """外向き HTTP (OpenAI SDK) の 1 試行あたりの上限を差し込む (Issue #313)。

    MAF の `OpenAIChatClient` は `timeout` を受けないので、内側の `AsyncOpenAI` を
    `with_options(timeout=...)` で差し替える (OpenAI SDK の公開 API)。放置すると
    SDK 既定の 600s になり、**詰まった 1 本が 10 分ワーカーを占有する**。
    差し込めなかった場合は黙って既定に戻らないよう警告を残す。
    """
    inner = getattr(client, "client", None)
    if inner is None or not hasattr(inner, "with_options"):
        logger.warning(
            "Could not apply HTTP timeout to chat client (%s) — SDK default applies",
            type(client).__name__,
        )
        return client
    client.client = inner.with_options(timeout=timeout)
    return client


def _build_client() -> BaseChatClient:
    settings = get_settings()
    # 外向き HTTP 1 試行の上限。SDK のリトライを跨いだ総時間は縛れないので、
    # 呼び出し側 (complete / chat / chat_stream) で asyncio.timeout も掛ける。
    timeout = settings.llm_request_timeout_seconds
    if settings.azure_openai_endpoint:
        if settings.use_managed_identity:
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            logger.info(
                "Using Azure OpenAI with Managed Identity: %s",
                settings.azure_openai_deployment,
            )
            return _with_http_timeout(
                OpenAIChatClient(
                    model=settings.azure_openai_deployment,
                    azure_endpoint=settings.azure_openai_endpoint,
                    api_version=settings.azure_openai_responses_api_version,
                    credential=DefaultAzureCredential(),
                ),
                timeout,
            )
        logger.info(
            "Using Azure OpenAI with API key: %s", settings.azure_openai_deployment
        )
        return _with_http_timeout(
            OpenAIChatClient(
                model=settings.azure_openai_deployment,
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_responses_api_version,
                api_key=settings.azure_openai_api_key,
            ),
            timeout,
        )
    logger.info("Using OpenAI: %s", settings.openai_model)
    return _with_http_timeout(
        OpenAIChatClient(model=settings.openai_model, api_key=settings.openai_api_key),
        timeout,
    )


async def complete(client: BaseChatClient, prompt: str) -> str:
    """単発のユーザープロンプト → 応答テキスト。

    注意: Message.contents は Sequence を取るため必ずリストで渡す
    (生 str を渡すと 1 文字ずつの content に分解される)。
    """
    async with asyncio.timeout(get_settings().llm_total_timeout_seconds):
        response = await client.get_response(
            [Message(role="user", contents=[prompt])],
            options=_options_for_model(_current_model()),
        )
    return response.text


async def chat(
    client: BaseChatClient,
    messages: Sequence[Message],
    *,
    tools: Sequence[FunctionTool] = (),
) -> ChatResponse:
    """会話履歴 (Message 列) → ChatResponse (workflow の会話ターン / 非ストリーミング)。

    **テキストではなく ChatResponse を返す**のは、ツールを載せた呼び出しの戻りに
    「実行されたツールの結果」と「承認待ちの function_approval_request」が
    メッセージとして含まれるため。呼び出し側はそれを見て履歴と API 契約に写す。
    """
    _apply_function_invocation_limits(client)
    async with asyncio.timeout(get_settings().llm_total_timeout_seconds):
        return await client.get_response(
            list(messages),
            options=_options_for_model(_current_model(), tools),
            middleware=_TOOL_MIDDLEWARE,
        )


async def chat_stream(
    client: BaseChatClient,
    messages: Sequence[Message],
    *,
    tools: Sequence[FunctionTool] = (),
) -> AsyncIterator[ChatResponseUpdate]:
    """chat のストリーミング版。update を逐次 yield する (テキスト差分は `update.text`)。

    テキストだけでなく update そのものを流すのは、ツール呼び出し・承認要求が
    text 以外の content として届くため。呼び出し側が `ChatResponse.from_updates`
    で最終形に畳む。

    上限は総時間ではなく**チャンク間の無音時間**で測る (Issue #313):
    長い応答を正常に流し切れる一方、上流が黙り込んだ接続は必ず切れる。
    総時間で切ると「正常に長い応答」を途中で殺してしまう。
    """
    _apply_function_invocation_limits(client)
    idle_timeout = get_settings().llm_stream_idle_timeout_seconds
    updates = client.get_response(
        list(messages),
        stream=True,
        options=_options_for_model(_current_model(), tools),
        middleware=_TOOL_MIDDLEWARE,
    ).__aiter__()
    while True:
        # timeout は __anext__ の待ちだけに掛ける (消費側の処理時間は含めない)
        async with asyncio.timeout(idle_timeout):
            try:
                update = await updates.__anext__()
            except StopAsyncIteration:
                return
        yield update
