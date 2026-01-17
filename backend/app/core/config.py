from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Settings
    api_title: str = "Template API"
    api_version: str = "0.1.0"
    debug: bool = False

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # AI Provider
    anthropic_api_key: str = ""
    default_model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096


@lru_cache
def get_settings() -> Settings:
    return Settings()
