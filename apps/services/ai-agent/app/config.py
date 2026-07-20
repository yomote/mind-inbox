from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Azure OpenAI (primary)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    # gpt-4o は Azure で新規デプロイ不可。GA は gpt-5 / gpt-5-mini（推論モデル）。
    azure_openai_deployment: str = "gpt-5-mini"
    # 推論モデル（max_completion_tokens 等）に対応する api_version。古い 2024-02-01 では通らない。
    azure_openai_api_version: str = "2025-04-01-preview"

    # OpenAI (fallback when Azure is not configured)
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"

    # Set to true in ACA (managed identity); false for local dev with API key
    use_managed_identity: bool = False

    app_name: str = "mind-inbox-ai-agent"
    log_level: str = "INFO"

    # VOICEVOX stub — HTTP endpoint for future TTS integration
    voicevox_url: str = "http://localhost:50021"
    voicevox_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
