from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Protocol

import structlog

from mootcourt.schemas.health import ComponentHealth, ReadinessResponse

logger = structlog.get_logger(__name__)


class HealthProbe(Protocol):
    async def ping(self) -> bool: ...


async def check_readiness(
    database: HealthProbe,
    search: HealthProbe,
    timeout_seconds: float,
    optional_probes: dict[str, HealthProbe] | None = None,
) -> ReadinessResponse:
    """并行检查关键依赖；异常只记录类型，不向客户端泄露连接信息。"""

    probes: dict[str, HealthProbe] = {
        "database": database,
        "elasticsearch": search,
        **(optional_probes or {}),
    }
    results = await asyncio.gather(
        *(
            _check_component(name, probe, timeout_seconds)
            for name, probe in probes.items()
        )
    )
    components = dict(zip(probes, results, strict=True))
    ready = all(item.status == "ok" for item in components.values())
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        service="mootcourt-api",
        components=components,
    )


async def _check_component(
    name: str,
    probe: HealthProbe,
    timeout_seconds: float,
) -> ComponentHealth:
    started = perf_counter()
    try:
        healthy = await asyncio.wait_for(probe.ping(), timeout=timeout_seconds)
    except Exception as exc:
        logger.warning(
            "readiness_component_failed",
            component=name,
            exception_type=type(exc).__name__,
        )
        healthy = False
    latency_ms = max(0, round((perf_counter() - started) * 1_000))
    return ComponentHealth(
        status="ok" if healthy else "unavailable",
        latency_ms=latency_ms,
    )
