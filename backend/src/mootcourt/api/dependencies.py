from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mootcourt.agents.factory import AgentProviderConfigurationError, build_agent_provider
from mootcourt.agents.providers import AgentProvider, StructuredAgentProvider
from mootcourt.core.auth import (
    AuthenticatedPrincipal,
    AuthenticationError,
    authenticate_bearer_token,
)
from mootcourt.core.config import Settings, get_settings
from mootcourt.core.redis import get_redis_client
from mootcourt.core.security import DIAGNOSTICS_KEY_HEADER, diagnostics_access_allowed
from mootcourt.db.models import PlatformUserModel
from mootcourt.db.session import get_engine, get_session_factory
from mootcourt.repositories.health import (
    DatabaseHealthRepository,
    ElasticsearchHealthRepository,
    RedisHealthRepository,
)
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
RuntimeStructuredAgentProvider = Annotated[StructuredAgentProvider, Depends(get_agent_provider)]


def get_database_health_probe() -> DatabaseHealthRepository:
    return DatabaseHealthRepository(get_engine())


def get_search_health_probe() -> ElasticsearchHealthRepository:
    return ElasticsearchHealthRepository(get_elasticsearch_client())


RuntimeDatabaseHealthProbe = Annotated[
    DatabaseHealthRepository,
    Depends(get_database_health_probe),
]
RuntimeSearchHealthProbe = Annotated[
    ElasticsearchHealthRepository,
    Depends(get_search_health_probe),
]


def get_redis_health_probe(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedisHealthRepository | None:
    if not settings.redis_url:
        return None
    return RedisHealthRepository(get_redis_client(settings.redis_url))


RuntimeRedisHealthProbe = Annotated[
    RedisHealthRepository | None,
    Depends(get_redis_health_probe),
]


def require_diagnostics_access(
    settings: Annotated[Settings, Depends(get_settings)],
    provided_key: Annotated[str | None, Header(alias=DIAGNOSTICS_KEY_HEADER)] = None,
) -> None:
    if diagnostics_access_allowed(provided_key, settings):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "diagnostics_auth_required",
            "message": "valid diagnostics credentials are required",
        },
        headers={"WWW-Authenticate": "ApiKey"},
    )


RuntimeDiagnosticsAccess = Annotated[None, Depends(require_diagnostics_access)]

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_authenticated_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthenticatedPrincipal:
    """Resolve the verified Supabase subject; authorization remains database-backed."""
    token = credentials.credentials if credentials is not None else None
    try:
        return await authenticate_bearer_token(token, settings)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    unit_of_work: RuntimeUnitOfWork,
    settings: Annotated[Settings, Depends(get_settings)],
) -> PlatformUserModel:
    """Provision a local profile once and keep all product authorization in MySQL."""
    user = await unit_of_work.identity.get_or_create_user(principal.subject, principal.email)
    if principal.subject in settings.auth_bootstrap_admin_subjects:
        # 只接受服务端环境白名单，不能信任 JWT 中的任意自定义管理员声明。
        await unit_of_work.identity.ensure_public_admin(user.id)
    return user


async def require_session_access(
    unit_of_work: RuntimeUnitOfWork,
    current_user: Annotated[PlatformUserModel, Depends(get_current_user)],
    session_id: str | None = None,
) -> None:
    """Allow the owner or an instructor/admin to access a known court session."""
    if session_id is None:
        return
    session = await unit_of_work.court_sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "session_not_found"}
        )
    if session.owner_user_id == current_user.id:
        return
    if await unit_of_work.identity.can_manage_user_sessions(current_user.id, session.owner_user_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail={"code": "session_access_denied"}
    )


RuntimeAuthenticatedPrincipal = Annotated[
    AuthenticatedPrincipal, Depends(require_authenticated_principal)
]
RuntimeCurrentUser = Annotated[PlatformUserModel, Depends(get_current_user)]


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
