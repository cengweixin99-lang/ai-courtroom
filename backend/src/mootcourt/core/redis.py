from __future__ import annotations

from functools import lru_cache
from typing import Any

_CLIENTS: set[Any] = set()

@lru_cache
def get_redis_client(redis_url: str) -> Any:
    """按 URL 缓存 Redis 客户端；空 URL 不会创建外部连接。"""
    if not redis_url:
        return None
    try:
        from redis.asyncio import Redis
    except ImportError as exc:
        raise RuntimeError(
            "REDIS_URL is configured but the redis Python dependency is not installed"
        ) from exc
    client = Redis.from_url(redis_url, decode_responses=False, health_check_interval=30)
    _CLIENTS.add(client)
    return client


async def dispose_redis() -> None:
    for client in tuple(_CLIENTS):
        await client.aclose()
        _CLIENTS.discard(client)
    get_redis_client.cache_clear()
