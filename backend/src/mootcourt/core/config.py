from functools import lru_cache

from pydantic import Field, SecretStr
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
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index_prefix: str = "mootcourt"

    llm_provider: str = "openai"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)
    llm_max_output_tokens: int = Field(default=2_000, ge=1, le=100_000)
    llm_input_cost_per_million_cny: float = Field(default=0, ge=0)
    llm_output_cost_per_million_cny: float = Field(default=0, ge=0)

    session_max_turns: int = Field(default=40, ge=1)
    session_max_tokens: int = Field(default=80_000, ge=1)
    session_max_seconds: int = Field(default=2_400, ge=1)
    session_max_cost_cny: float = Field(default=20, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
