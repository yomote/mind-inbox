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

from agent_framework import BaseChatClient, Message
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions

from .config import get_settings

logger = logging.getLogger(__name__)

_client: BaseChatClient | None = None

# temperature / max_tokens を受け付けない推論モデルの接頭辞 (#55)。
# Azure ではデプロイ名がモデル名と一致する運用 (gpt-4o / gpt-5-mini 等) を前提とする。
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _options_for_model(model: str | None) -> OpenAIChatOptions:
    """モデル名に応じた既定オプション。推論モデルは temperature / max_tokens 非対応のため付けない。"""
    if model and model.lower().startswith(_REASONING_MODEL_PREFIXES):
        return OpenAIChatOptions()
    return OpenAIChatOptions(temperature=0.7, max_tokens=1024)


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


async def chat(client: BaseChatClient, messages: Sequence[Message]) -> str:
    """会話履歴 (Message 列) → 応答テキスト (workflow の RESPOND / 非ストリーミング)。"""
    async with asyncio.timeout(get_settings().llm_total_timeout_seconds):
        response = await client.get_response(
            list(messages), options=_options_for_model(_current_model())
        )
    return response.text


async def chat_stream(
    client: BaseChatClient, messages: Sequence[Message]
) -> AsyncIterator[str]:
    """chat のストリーミング版。トークン (チャンク) 文字列を逐次 yield する。

    上限は総時間ではなく**チャンク間の無音時間**で測る (Issue #313):
    長い応答を正常に流し切れる一方、上流が黙り込んだ接続は必ず切れる。
    総時間で切ると「正常に長い応答」を途中で殺してしまう。
    """
    idle_timeout = get_settings().llm_stream_idle_timeout_seconds
    updates = client.get_response(
        list(messages), stream=True, options=_options_for_model(_current_model())
    ).__aiter__()
    while True:
        # timeout は __anext__ の待ちだけに掛ける (消費側の処理時間は含めない)
        async with asyncio.timeout(idle_timeout):
            try:
                update = await updates.__anext__()
            except StopAsyncIteration:
                return
        text = update.text or ""
        if text:
            yield text
