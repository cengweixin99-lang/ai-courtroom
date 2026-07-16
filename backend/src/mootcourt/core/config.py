from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MootCourt Lab API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    database_url: str = "mysql+aiomysql://mootcourt:change-me@localhost:3306/mootcourt"
    opensearch_url: str = "http://localhost:9200"
    opensearch_index_prefix: str = "mootcourt"

    llm_provider: str = "openai"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""

    session_max_turns: int = Field(default=40, ge=1)
    session_max_tokens: int = Field(default=80_000, ge=1)
    session_max_seconds: int = Field(default=2_400, ge=1)
    session_max_cost_cny: float = Field(default=20, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
