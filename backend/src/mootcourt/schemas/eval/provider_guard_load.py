from __future__ import annotations

from pydantic import Field

from mootcourt.schemas.agents import StrictAgentModel


class ProviderGuardLoadReport(StrictAgentModel):
    namespace: str
    replica_count: int = Field(ge=2)
    request_count: int = Field(ge=1)
    configured_max_concurrency: int = Field(ge=1)
    accepted_count: int = Field(ge=0)
    rejection_codes: dict[str, int]
    unexpected_error_count: int = Field(ge=0)
    observed_max_concurrency: int = Field(ge=0)
    acquire_latency_p95_ms: float = Field(ge=0)
    acquire_latency_p99_ms: float = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    circuit_shared: bool
    passed: bool
