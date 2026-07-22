from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from mootcourt.api.dependencies import (
    RuntimeDatabaseHealthProbe,
    RuntimeRedisHealthProbe,
    RuntimeSearchHealthProbe,
)
from mootcourt.core.config import Settings, get_settings
from mootcourt.schemas.health import HealthResponse, ReadinessResponse
from mootcourt.services.health import check_readiness

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    operation_id="health_check",
    summary="检查 API 存活状态",
    response_description="当前 API 服务状态和服务器时间",
)
async def health() -> HealthResponse:
    """提供不依赖数据库和外部服务的进程存活检查。"""
    return HealthResponse(status="ok", service="mootcourt-api", timestamp=datetime.now(UTC))


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    operation_id="readiness_check",
    summary="检查 API 关键依赖就绪状态",
    response_description="数据库与 Elasticsearch 的可用状态和探测耗时",
    responses={503: {"description": "一个或多个关键依赖当前不可用"}},
)
async def readiness(
    response: Response,
    database: RuntimeDatabaseHealthProbe,
    search: RuntimeSearchHealthProbe,
    redis: RuntimeRedisHealthProbe,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    """供容器编排执行就绪检查；失败时返回 503，但不暴露连接地址或异常文本。"""

    result = await check_readiness(
        database,
        search,
        settings.readiness_timeout_seconds,
        {"redis": redis} if redis is not None else None,
    )
    if result.status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
