"""
Microsoft Agent Framework の chat client シングルトン (ADR 0016, M1)。

extractor / planner (単発 structured 呼び出し系 = `complete`) と
workflow.py (/chat 系 = `chat` / `chat_stream`) が共有する、
このサービス唯一の LLM 呼び出し面 (M1-5 で SK 依存を除去し一本化)。
"""

from __future__ import annotations

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


def _build_client() -> BaseChatClient:
    settings = get_settings()
    if settings.azure_openai_endpoint:
        if settings.use_managed_identity:
            from azure.identity import DefaultAzureCredential  # type: ignore[import]

            logger.info(
                "Using Azure OpenAI with Managed Identity: %s",
                settings.azure_openai_deployment,
            )
            return OpenAIChatClient(
                model=settings.azure_openai_deployment,
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_responses_api_version,
                credential=DefaultAzureCredential(),
            )
        logger.info(
            "Using Azure OpenAI with API key: %s", settings.azure_openai_deployment
        )
        return OpenAIChatClient(
            model=settings.azure_openai_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_responses_api_version,
            api_key=settings.azure_openai_api_key,
        )
    logger.info("Using OpenAI: %s", settings.openai_model)
    return OpenAIChatClient(
        model=settings.openai_model, api_key=settings.openai_api_key
    )


async def complete(client: BaseChatClient, prompt: str) -> str:
    """単発のユーザープロンプト → 応答テキスト。

    注意: Message.contents は Sequence を取るため必ずリストで渡す
    (生 str を渡すと 1 文字ずつの content に分解される)。
    """
    response = await client.get_response(
        [Message(role="user", contents=[prompt])],
        options=_options_for_model(_current_model()),
    )
    return response.text


async def chat(client: BaseChatClient, messages: Sequence[Message]) -> str:
    """会話履歴 (Message 列) → 応答テキスト (workflow の RESPOND / 非ストリーミング)。"""
    response = await client.get_response(
        list(messages), options=_options_for_model(_current_model())
    )
    return response.text


async def chat_stream(
    client: BaseChatClient, messages: Sequence[Message]
) -> AsyncIterator[str]:
    """chat のストリーミング版。トークン (チャンク) 文字列を逐次 yield する。"""
    async for update in client.get_response(
        list(messages), stream=True, options=_options_for_model(_current_model())
    ):
        text = update.text or ""
        if text:
            yield text
