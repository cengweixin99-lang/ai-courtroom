from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import AgentTraceModel

AgentTraceRecord = AgentTraceModel


class SqlAlchemyAgentTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        session_id: str,
        actor_role: str,
        participant_id: str | None,
        provider: str,
        model: str,
        status: str,
        repair_count: int,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any] | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        estimated_cost_cny: float,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentTraceModel:
        trace = AgentTraceModel(
            session_id=session_id,
            actor_role=actor_role,
            participant_id=participant_id,
            provider=provider,
            model=model,
            status=status,
            repair_count=repair_count,
            request_payload=request_payload,
            response_payload=response_payload,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_cny=estimated_cost_cny,
            error_code=error_code,
            error_message=error_message,
        )
        self._session.add(trace)
        await self._session.flush()
        await self._session.refresh(trace)
        return trace

    async def list_for_session(self, session_id: str) -> list[AgentTraceModel]:
        return list(
            await self._session.scalars(
                select(AgentTraceModel)
                .where(AgentTraceModel.session_id == session_id)
                .order_by(AgentTraceModel.created_at, AgentTraceModel.id)
            )
        )
