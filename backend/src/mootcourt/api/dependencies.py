from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

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
    # E2.1 先以确定性 Provider 验证安全与事务闭环；配置真实模型后必须显式接入对应适配器。
    if not settings.llm_model or settings.llm_provider == "fake":
        return FakeAgentProvider()
    raise RuntimeError(f"LLM provider adapter is not implemented: {settings.llm_provider}")


RuntimeAgentProvider = Annotated[AgentProvider, Depends(get_agent_provider)]
