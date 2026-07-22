from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mootcourt.db.models import AgentTraceModel

AgentTraceRecord = AgentTraceModel


@dataclass(frozen=True, slots=True)
class SessionAgentUsage:
    trace_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_cny: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


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
        output_normalized: bool,
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
            output_normalized=output_normalized,
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

    async def list_legacy(self, *, limit: int = 500) -> list[AgentTraceModel]:
        """读取尚未采用新脱敏 schema 的历史 Trace，供维护任务分批处理。"""
        schema_version = AgentTraceModel.request_payload["schema_version"].as_string()
        rows = await self._session.scalars(
            select(AgentTraceModel)
            .where(
                or_(
                    schema_version.is_(None),
                    ~schema_version.in_(
                        ["agent-trace-redacted-v1", "agent-trace-none-v1"]
                    ),
                )
            )
            .order_by(AgentTraceModel.created_at, AgentTraceModel.id)
            .limit(limit)
        )
        return list(rows)

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(AgentTraceModel).where(AgentTraceModel.created_at < cutoff)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def usage_for_session(
        self, session_id: str, *, lock_rows: bool = False
    ) -> SessionAgentUsage:
        if lock_rows:
            # 会话行锁之后再锁定读取 Trace，确保并发调用看到最新已提交的预算消耗。
            rows = list(
                await self._session.scalars(
                    select(AgentTraceModel)
                    .where(AgentTraceModel.session_id == session_id)
                    .with_for_update()
                )
            )
            return SessionAgentUsage(
                trace_count=len(rows),
                input_tokens=sum(item.input_tokens for item in rows),
                output_tokens=sum(item.output_tokens for item in rows),
                latency_ms=sum(item.latency_ms for item in rows),
                estimated_cost_cny=sum(item.estimated_cost_cny for item in rows),
            )
        row = (
            await self._session.execute(
                select(
                    func.count(AgentTraceModel.id).label("trace_count"),
                    func.coalesce(func.sum(AgentTraceModel.input_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AgentTraceModel.output_tokens), 0).label(
                        "output_tokens"
                    ),
                    func.coalesce(func.sum(AgentTraceModel.latency_ms), 0).label("latency_ms"),
                    func.coalesce(func.sum(AgentTraceModel.estimated_cost_cny), 0.0).label(
                        "estimated_cost_cny"
                    ),
                ).where(AgentTraceModel.session_id == session_id)
            )
        ).one()
        return SessionAgentUsage(
            trace_count=int(row.trace_count),
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            latency_ms=int(row.latency_ms),
            estimated_cost_cny=float(row.estimated_cost_cny),
        )
