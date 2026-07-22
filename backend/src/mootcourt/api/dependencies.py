from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status

from mootcourt.agents.factory import AgentProviderConfigurationError, build_agent_provider
from mootcourt.agents.providers import AgentProvider, StructuredAgentProvider
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

# 流式响应生成期间必须保持数据库会话有效，结束后再由依赖生命周期统一收尾。
StreamingUnitOfWork = Annotated[
    SqlAlchemyUnitOfWork,
    Depends(get_unit_of_work, scope="request"),
]


def get_agent_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentProvider:
    # Fake 只能被测试或显式开发配置启用；运行环境不得因漏配模型而静默返回模板话术。
    try:
        return build_agent_provider(settings, allow_fake=True)
    except AgentProviderConfigurationError as exc:
        code = "llm_provider_unsupported" if "unsupported" in str(exc) else "llm_not_configured"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": code, "message": str(exc)},
        ) from exc


RuntimeAgentProvider = Annotated[AgentProvider, Depends(get_agent_provider)]
RuntimeStructuredAgentProvider = Annotated[
    StructuredAgentProvider, Depends(get_agent_provider)
]


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
