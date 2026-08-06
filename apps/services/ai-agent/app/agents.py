"""
Microsoft Agent Framework の chat client シングルトン (ADR 0016, M1)。

extractor / organizer / planner (単発 structured 呼び出し系) が共有する。
/chat 系 (workflow.py) は M1-3 で移行するまで kernel.py (Semantic Kernel) を
使い続ける — 変化を 1 度に 1 種類にするため (implementation_plan_v2 §0.2)。
"""

from __future__ import annotations

import logging

from agent_framework import BaseChatClient, Message
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions

from .config import get_settings

logger = logging.getLogger(__name__)

_client: BaseChatClient | None = None


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
                api_version=settings.azure_openai_api_version,
                credential=DefaultAzureCredential(),
            )
        logger.info(
            "Using Azure OpenAI with API key: %s", settings.azure_openai_deployment
        )
        return OpenAIChatClient(
            model=settings.azure_openai_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
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
        options=OpenAIChatOptions(temperature=0.7, max_tokens=1024),
    )
    return response.text
