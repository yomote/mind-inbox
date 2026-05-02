from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    voicevox_engine_base_url: str = "http://localhost:50021"
    port: int = 8080

    class Config:
        env_file = ".env"


settings = Settings()
