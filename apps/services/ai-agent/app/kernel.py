"""
Semantic Kernel のシングルトン。

workflow / organizer / planner が共有する。
"""
from __future__ import annotations

import logging

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import (
    AzureChatCompletion,
    AzureChatPromptExecutionSettings,
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)

from .config import get_settings
from .tools import READONLY_PLUGIN, SIDEEFFECT_PLUGIN

logger = logging.getLogger(__name__)

_kernel: Kernel | None = None


def get_kernel() -> Kernel:
    global _kernel
    if _kernel is None:
        _kernel = _build_kernel()
    return _kernel


def get_execution_settings() -> (
    AzureChatPromptExecutionSettings | OpenAIChatPromptExecutionSettings
):
    settings = get_settings()
    if settings.azure_openai_endpoint:
        return AzureChatPromptExecutionSettings(temperature=0.7, max_tokens=1024)
    return OpenAIChatPromptExecutionSettings(temperature=0.7, max_tokens=1024)


def _build_kernel() -> Kernel:
    settings = get_settings()
    kernel = Kernel()
    try:
        if settings.azure_openai_endpoint:
            if settings.use_managed_identity:
                from azure.identity.aio import (  # type: ignore[import]
                    DefaultAzureCredential,
                    get_bearer_token_provider,
                )
                token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(),
                    "https://cognitiveservices.azure.com/.default",
                )
                logger.info(
                    "Using Azure OpenAI with Managed Identity: %s",
                    settings.azure_openai_deployment,
                )
                kernel.add_service(
                    AzureChatCompletion(
                        service_id="chat",
                        deployment_name=settings.azure_openai_deployment,
                        endpoint=settings.azure_openai_endpoint,
                        ad_token_provider=token_provider,
                        api_version=settings.azure_openai_api_version,
                    )
                )
            else:
                logger.info(
                    "Using Azure OpenAI with API key: %s",
                    settings.azure_openai_deployment,
                )
                kernel.add_service(
                    AzureChatCompletion(
                        service_id="chat",
                        deployment_name=settings.azure_openai_deployment,
                        endpoint=settings.azure_openai_endpoint,
                        api_key=settings.azure_openai_api_key,
                        api_version=settings.azure_openai_api_version,
                    )
                )
        else:
            logger.info("Using OpenAI: %s", settings.openai_model)
            kernel.add_service(
                OpenAIChatCompletion(
                    service_id="chat",
                    ai_model_id=settings.openai_model,
                    api_key=settings.openai_api_key,
                )
            )
    except Exception as exc:
        logger.error("Kernel setup failed — LLM calls will fail: %s", exc)

    kernel.add_plugin(READONLY_PLUGIN, plugin_name="readonly")
    kernel.add_plugin(SIDEEFFECT_PLUGIN, plugin_name="sideeffect")
    return kernel
