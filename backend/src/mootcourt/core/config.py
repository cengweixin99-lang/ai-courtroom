from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    access_log_enabled: bool = True
    metrics_enabled: bool = True
    metrics_path: str = Field(default="/metrics", pattern=r"^/[A-Za-z0-9/_-]+$")
    readiness_timeout_seconds: float = Field(default=3, gt=0, le=30)
    shutdown_drain_timeout_seconds: float = Field(default=30, gt=0, le=300)
    diagnostics_api_key: SecretStr = SecretStr("")
    agent_trace_payload_mode: Literal["full", "redacted", "none"] = "redacted"
    trace_redaction_hmac_key: SecretStr = SecretStr("")
    idempotency_encryption_key: SecretStr = SecretStr("")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    redis_url: str = ""
    redis_key_prefix: str = Field(default="mootcourt:provider", pattern=r"^[A-Za-z0-9:_-]{1,64}$")

    database_url: str = "mysql+aiomysql://mootcourt:change-me@localhost:3306/mootcourt"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index_prefix: str = "mootcourt"
    elasticsearch_legal_index_version: str = "v1"
    elasticsearch_timeout_seconds: float = Field(default=10, gt=0, le=120)
    legal_embedding_enabled: bool = False
    legal_embedding_registry_path: str = "knowledge/legal/embedding_models.json"
    legal_embedding_provider: str = "openai-compatible"
    legal_embedding_model: str = ""
    legal_embedding_api_key: SecretStr = SecretStr("")
    legal_embedding_base_url: str = ""
    legal_embedding_version: str = "disabled"
    legal_embedding_dimensions: int = Field(default=1024, ge=8, le=4096)
    legal_embedding_timeout_seconds: float = Field(default=30, gt=0, le=300)
    legal_embedding_batch_size: int = Field(default=32, ge=1, le=256)
    legal_vector_similarity_threshold: float = Field(default=0.78, ge=-1, le=1)
    legal_hybrid_candidate_multiplier: int = Field(default=4, ge=1, le=20)
    legal_rrf_rank_constant: int = Field(default=60, ge=1, le=1000)

    llm_provider: str = "openai"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)
    llm_max_output_tokens: int = Field(default=2_000, ge=1, le=100_000)
    llm_max_input_tokens: int = Field(default=24_000, ge=1_000, le=1_000_000)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_incomplete_retries: int = Field(default=1, ge=0, le=3)
    llm_retry_base_delay_seconds: float = Field(default=0.5, ge=0, le=10)
    llm_max_concurrency: int = Field(default=8, ge=1, le=1_000)
    llm_requests_per_second: float = Field(default=0, ge=0, le=10_000)
    llm_queue_timeout_seconds: float = Field(default=5, gt=0, le=300)
    llm_circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    llm_circuit_recovery_seconds: float = Field(default=30, gt=0, le=3_600)
    llm_response_format: Literal["json_schema", "json_object", "plain_json"] = "json_schema"
    llm_max_tokens_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    llm_enable_thinking: bool | None = None
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_input_cost_per_million_cny: float = Field(default=0, ge=0)
    llm_output_cost_per_million_cny: float = Field(default=0, ge=0)
    agent_invocation_lease_seconds: int = Field(default=900, ge=30, le=3_600)
    agent_trace_retention_days: int = Field(default=30, ge=1, le=3_650)
    agent_invocation_retention_days: int = Field(default=7, ge=1, le=3_650)

    session_max_turns: int = Field(default=40, ge=1)
    session_budget_enforcement_enabled: bool = False
    session_max_tokens: int = Field(default=80_000, ge=1)
    session_max_seconds: int = Field(default=2_400, ge=1)
    session_max_cost_cny: float = Field(default=20, gt=0)

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if self.redis_url and not self.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        if self.app_env.lower() != "production":
            return self
        diagnostics_key = self.diagnostics_api_key.get_secret_value()
        if len(diagnostics_key) < 32:
            raise ValueError(
                "DIAGNOSTICS_API_KEY must contain at least 32 characters in production"
            )
        if self.agent_trace_payload_mode == "full":
            raise ValueError("AGENT_TRACE_PAYLOAD_MODE=full is forbidden in production")
        redaction_key = self.trace_redaction_hmac_key.get_secret_value()
        if self.agent_trace_payload_mode == "redacted" and len(redaction_key) < 32:
            raise ValueError(
                "TRACE_REDACTION_HMAC_KEY must contain at least 32 characters in production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
