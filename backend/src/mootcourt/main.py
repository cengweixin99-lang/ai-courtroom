from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mootcourt.agents.provider_resilience import drain_provider_calls, resume_provider_calls
from mootcourt.api.router import api_router
from mootcourt.core.config import get_settings
from mootcourt.core.logging import configure_logging
from mootcourt.core.observability import ObservabilityMiddleware, metrics_response
from mootcourt.core.redis import dispose_redis
from mootcourt.db.session import dispose_engine
from mootcourt.search.client import dispose_elasticsearch_client

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    await resume_provider_calls()
    try:
        yield
    finally:
        drained = await drain_provider_calls(settings.shutdown_drain_timeout_seconds)
        if not drained:
            logger.warning(
                "agent_provider_drain_timed_out",
                timeout_seconds=settings.shutdown_drain_timeout_seconds,
            )
        await dispose_elasticsearch_client()
        await dispose_redis()
        await dispose_engine()


settings = get_settings()
openapi_tags = [
    {"name": "system", "description": "服务运行状态检查。"},
    {
        "name": "cases",
        "description": "读取已导入运行库、并按庭审角色过滤的案件内容。",
    },
    {
        "name": "sessions",
        "description": "创建和恢复庭审会话，读取事件日志并执行确定性庭审动作。",
    },
    {
        "name": "agents",
        "description": "执行受状态机约束的角色 Agent 回合并读取调用 Trace。",
    },
    {
        "name": "legal-search",
        "description": "按案件 LegalProfile 检索经审核、版本有效的候选法律依据。",
    },
]
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="虚构案件教学模拟 API，不构成现实裁判或法律意见。",
    openapi_tags=openapi_tags,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Idempotency-Key", "Idempotency-Replayed"],
)
app.add_middleware(ObservabilityMiddleware, access_log_enabled=settings.access_log_enabled)
app.include_router(api_router, prefix=settings.api_prefix)
if settings.metrics_enabled:
    app.add_route(settings.metrics_path, metrics_response, methods=["GET"], include_in_schema=False)
