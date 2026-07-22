from __future__ import annotations

from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseRevisionReader(Protocol):
    async def get_database_revision(self) -> str | None: ...


class DeploymentRepository:
    """只读交付环境元数据，避免验收 Service 直接执行数据库语句。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_database_revision(self) -> str | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            value = result.scalar_one_or_none()
            return str(value) if value is not None else None
