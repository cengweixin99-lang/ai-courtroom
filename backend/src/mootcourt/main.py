from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mootcourt.api.router import api_router
from mootcourt.core.config import get_settings
from mootcourt.core.logging import configure_logging
from mootcourt.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    try:
        yield
    finally:
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
)
app.include_router(api_router, prefix=settings.api_prefix)
