from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # class-based Config は Pydantic V2 で deprecated (V3 で削除)。
    # ai-agent 側 (app/config.py) と同じ SettingsConfigDict に揃えてある。
    model_config = SettingsConfigDict(env_file=".env")

    voicevox_engine_base_url: str = "http://localhost:50021"
    port: int = 8080


settings = Settings()
