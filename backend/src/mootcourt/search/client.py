from functools import lru_cache

from elasticsearch import AsyncElasticsearch

from mootcourt.core.config import get_settings


@lru_cache
def get_elasticsearch_client() -> AsyncElasticsearch:
    settings = get_settings()
    return AsyncElasticsearch(
        settings.elasticsearch_url,
        request_timeout=settings.elasticsearch_timeout_seconds,
        retry_on_timeout=True,
        max_retries=2,
    )


async def dispose_elasticsearch_client() -> None:
    if get_elasticsearch_client.cache_info().currsize:
        await get_elasticsearch_client().close()
    get_elasticsearch_client.cache_clear()
