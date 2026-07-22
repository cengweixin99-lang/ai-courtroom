from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import AgentInvocationModel

AgentInvocationRecord = AgentInvocationModel


class SqlAlchemyAgentInvocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_key(
        self, session_id: str, idempotency_key: str
    ) -> AgentInvocationModel | None:
        return cast(
            AgentInvocationModel | None,
            await self._session.scalar(
                select(AgentInvocationModel).where(
                    AgentInvocationModel.session_id == session_id,
                    AgentInvocationModel.idempotency_key == idempotency_key,
                )
            ),
        )

    async def get_for_update(self, invocation_id: str) -> AgentInvocationModel | None:
        return cast(
            AgentInvocationModel | None,
            await self._session.scalar(
                select(AgentInvocationModel)
                .where(AgentInvocationModel.id == invocation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    def add(
        self,
        *,
        session_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        status: str,
        lease_token: str,
        lease_expires_at: datetime,
    ) -> AgentInvocationModel:
        invocation = AgentInvocationModel(
            session_id=session_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status=status,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
        )
        self._session.add(invocation)
        return invocation

    async def flush(self) -> None:
        await self._session.flush()

    async def delete_finished_older_than(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(AgentInvocationModel).where(
                AgentInvocationModel.updated_at < cutoff,
                AgentInvocationModel.status.in_(["completed", "abandoned"]),
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)
