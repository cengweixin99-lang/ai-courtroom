from __future__ import annotations

from typing import Any

from elasticsearch import AsyncElasticsearch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseHealthRepository:
    """使用独立短查询检查数据库连接，不进入业务事务。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> bool:
        async with self._engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            return bool(result.scalar_one() == 1)


class ElasticsearchHealthRepository:
    """检查检索服务是否可响应；不要求索引已包含特定案件。"""

    def __init__(self, client: AsyncElasticsearch) -> None:
        self._client = client

    async def ping(self) -> bool:
        # 就绪检查由上层统一控制超时，不在单次探针内叠加客户端重试放大流量。
        return bool(await self._client.options(max_retries=0).ping())


class RedisHealthRepository:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def ping(self) -> bool:
        return bool(await self._client.ping())
