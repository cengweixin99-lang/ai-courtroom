from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.repositories.agent_invocations import SqlAlchemyAgentInvocationRepository
from mootcourt.repositories.agent_traces import SqlAlchemyAgentTraceRepository
from mootcourt.repositories.case_packages import SqlAlchemyCasePackageRepository
from mootcourt.repositories.court_sessions import SqlAlchemyCourtSessionRepository
from mootcourt.repositories.identity import SqlAlchemyIdentityRepository
from mootcourt.repositories.legal_search_traces import SqlAlchemyLegalSearchTraceRepository


# 统一管理事务
class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.agent_traces = SqlAlchemyAgentTraceRepository(session)
        self.agent_invocations = SqlAlchemyAgentInvocationRepository(session)
        self.case_packages = SqlAlchemyCasePackageRepository(session)
        self.court_sessions = SqlAlchemyCourtSessionRepository(session)
        self.identity = SqlAlchemyIdentityRepository(session)
        self.legal_search_traces = SqlAlchemyLegalSearchTraceRepository(session)
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
