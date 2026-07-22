from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from mootcourt.schemas.agents import StrictAgentModel


class DeliveryAcceptanceCheck(StrictAgentModel):
    key: str
    label: str
    passed: bool
    detail: str
    latency_ms: float = Field(ge=0)


class DeliveryAcceptanceReport(StrictAgentModel):
    suite: Literal["mootcourt_delivery_acceptance"] = "mootcourt_delivery_acceptance"
    mode: Literal["smoke", "full"]
    generated_at: datetime
    api_base_url: str
    web_url: str
    elasticsearch_url: str
    case_id: str
    package_version: str | None
    session_id: str | None = None
    final_phase: str | None = None
    checks: list[DeliveryAcceptanceCheck]
    failures: list[str]
    artifacts: dict[str, Any] = Field(default_factory=dict)
    passed: bool
