from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status

from mootcourt.agents.openai_compatible import OpenAICompatibleProvider
from mootcourt.agents.providers import AgentProvider, FakeAgentProvider
from mootcourt.core.config import Settings, get_settings
from mootcourt.db.session import get_session_factory
from mootcourt.repositories.legal_search import (
    ElasticsearchLegalSearchRepository,
    LegalSearchRepository,
)
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.search.client import get_elasticsearch_client
from mootcourt.search.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    build_embedding_provider,
)


async def get_unit_of_work() -> AsyncIterator[SqlAlchemyUnitOfWork]:
    async with get_session_factory()() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        try:
            yield unit_of_work  # 注入到路由函数
            await unit_of_work.commit()  # 路由执行成功后自动提交
        except Exception:
            await unit_of_work.rollback()  # 异常时自动回滚
            raise


# 依赖注入
# 数据库事务必须在响应发送前提交，避免客户端收到成功响应后立即读取到旧状态。
RuntimeUnitOfWork = Annotated[
    SqlAlchemyUnitOfWork,
    Depends(get_unit_of_work, scope="function"),
]


def get_agent_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentProvider:
    # 未配置模型时保持确定性 Fake 路径，确保本地开发和 CI 不依赖外部网络。
    if not settings.llm_model or settings.llm_provider == "fake":
        return FakeAgentProvider()
    if settings.llm_provider not in {"openai", "openai-compatible"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "llm_provider_unsupported", "message": settings.llm_provider},
        )
    api_key = settings.llm_api_key.get_secret_value()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "llm_not_configured", "message": "LLM_API_KEY is required"},
        )
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url or "https://api.openai.com/v1",
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        input_cost_per_million_cny=settings.llm_input_cost_per_million_cny,
        output_cost_per_million_cny=settings.llm_output_cost_per_million_cny,
    )


RuntimeAgentProvider = Annotated[AgentProvider, Depends(get_agent_provider)]


def get_legal_search_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LegalSearchRepository:
    index_name = (
        f"{settings.elasticsearch_index_prefix}-legal-articles-"
        f"{settings.elasticsearch_legal_index_version}"
    )
    return ElasticsearchLegalSearchRepository(
        get_elasticsearch_client(),
        index_name,
        embedding_dimensions=(
            settings.legal_embedding_dimensions if settings.legal_embedding_enabled else None
        ),
        vector_similarity_threshold=settings.legal_vector_similarity_threshold,
        hybrid_candidate_multiplier=settings.legal_hybrid_candidate_multiplier,
        rrf_rank_constant=settings.legal_rrf_rank_constant,
    )


RuntimeLegalSearchRepository = Annotated[
    LegalSearchRepository,
    Depends(get_legal_search_repository),
]


def get_legal_embedding_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmbeddingProvider | None:
    try:
        return build_embedding_provider(settings)
    except EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "legal_embedding_not_configured", "message": str(exc)},
        ) from exc


RuntimeLegalEmbeddingProvider = Annotated[
    EmbeddingProvider | None,
    Depends(get_legal_embedding_provider),
]
