from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mootcourt.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


async def dispose_engine() -> None:
    """在事件循环关闭前释放连接池，避免异步数据库连接残留。"""
    await get_engine().dispose()
    # 先释放再清缓存，后续测试或同进程命令才能按新配置创建全新的引擎。
    get_session_factory.cache_clear()
    get_engine.cache_clear()
