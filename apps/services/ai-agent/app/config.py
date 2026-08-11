from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Azure OpenAI (primary)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    # Responses API 用 (Microsoft Agent Framework の全 LLM 呼び出しが使う)。
    # chat completions 用の api-version (旧 azure_openai_api_version) とは**別物**。
    # 2026-08-10、共用していたため /extract が `400 API version not supported` で
    # 常時 500 になっていた。旧設定は SK 除去 (M1-5) で唯一の利用者 (kernel.py) が
    # 消えたため撤去。
    # 値は agent_framework_openai の既定 (DEFAULT_AZURE_OPENAI_RESPONSES_API_VERSION) に合わせる。
    azure_openai_responses_api_version: str = "preview"

    # OpenAI (fallback when Azure is not configured)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Set to true in ACA (managed identity); false for local dev with API key
    use_managed_identity: bool = False

    # Cosmos DB (ADR 0030 / #188) — 会話セッション・承認レコード・MAF checkpoint の永続化。
    # **cosmos_endpoint の有無が分岐点**: 未設定なら従来どおり in-memory で動く
    # (ローカル / テストの既定 = BFF の COSMOS_ENDPOINT と同じ流儀 / ADR 0030 D7)。
    # 認証は Managed Identity のみ — キーは disableLocalAuth: true で殺されている (D3)。
    # コンテナは bicep (bootstrap-core.bicep) が TTL 付きで宣言する。名前がズレると
    # 「デプロイは通るが実行時 404」になるので、既定値は bicep 側の宣言と揃えること。
    cosmos_endpoint: str = ""
    cosmos_database: str = "mindinbox"
    cosmos_sessions_container: str = "sessions"
    cosmos_approvals_container: str = "approvals"
    cosmos_checkpoints_container: str = "checkpoints"

    app_name: str = "mind-inbox-ai-agent"
    log_level: str = "INFO"

    # VOICEVOX stub — HTTP endpoint for future TTS integration
    voicevox_url: str = "http://localhost:50021"
    voicevox_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
