from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status

from mootcourt.agents.openai_compatible import OpenAICompatibleProvider
from mootcourt.agents.providers import AgentProvider, FakeAgentProvider
from mootcourt.core.config import Settings, get_settings
from mootcourt.db.session import get_session_factory
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork


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
RuntimeUnitOfWork = Annotated[SqlAlchemyUnitOfWork, Depends(get_unit_of_work)]


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
