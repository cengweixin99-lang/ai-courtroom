from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    timestamp: datetime


class ComponentHealth(BaseModel):
    status: Literal["ok", "unavailable"]
    latency_ms: int


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    service: str
    components: dict[str, ComponentHealth]
