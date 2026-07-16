from datetime import UTC, datetime

from fastapi import APIRouter

from mootcourt.schemas.health import HealthResponse

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
