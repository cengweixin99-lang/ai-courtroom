from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from mootcourt.core.trace_security import protect_agent_trace_payloads


class AgentTraceMaintenanceRepository(Protocol):
    """维护任务实际依赖的 Trace 仓储最小接口。"""

    async def list_legacy(self, *, limit: int) -> Any: ...

    async def delete_older_than(self, cutoff: datetime) -> int: ...


class AgentInvocationMaintenanceRepository(Protocol):
    """维护任务实际依赖的幂等调用仓储最小接口。"""

    async def delete_finished_older_than(self, cutoff: datetime) -> int: ...


class AgentDataMaintenanceUnitOfWork(Protocol):
    """避免维护服务绑定 SQLAlchemy 实现，便于批处理与测试替换。"""

    @property
    def agent_traces(self) -> AgentTraceMaintenanceRepository: ...

    @property
    def agent_invocations(self) -> AgentInvocationMaintenanceRepository: ...

    async def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TraceRedactionResult:
    processed: int
    failed: int


@dataclass(frozen=True, slots=True)
class AgentDataPurgeResult:
    traces_deleted: int
    invocations_deleted: int


async def redact_existing_agent_traces(
    unit_of_work: AgentDataMaintenanceUnitOfWork,
    *,
    hmac_key: str,
    batch_size: int = 500,
) -> TraceRedactionResult:
    """把历史完整 Trace 转换为当前 redacted schema，不输出正文。"""
    processed = 0
    failed = 0
    while True:
        rows = await unit_of_work.agent_traces.list_legacy(limit=batch_size)
        if not rows:
            break
        for trace in rows:
            try:
                request, response = protect_agent_trace_payloads(
                    trace.request_payload,
                    trace.response_payload,
                    mode="redacted",
                    hmac_key=hmac_key,
                )
                trace.request_payload = request
                trace.response_payload = response
                trace.error_message = None
                processed += 1
            except (TypeError, ValueError):
                failed += 1
        await unit_of_work.commit()
        # 异常记录保持原样供人工检查，避免下一轮反复处理造成死循环。
        if failed:
            break
    return TraceRedactionResult(processed=processed, failed=failed)


async def purge_agent_data(
    unit_of_work: AgentDataMaintenanceUnitOfWork,
    *,
    older_than_days: int,
    invocation_older_than_days: int | None = None,
) -> AgentDataPurgeResult:
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    invocation_cutoff = datetime.now(UTC) - timedelta(
        days=invocation_older_than_days or older_than_days
    )
    traces = await unit_of_work.agent_traces.delete_older_than(cutoff)
    invocations = await unit_of_work.agent_invocations.delete_finished_older_than(invocation_cutoff)
    await unit_of_work.commit()
    return AgentDataPurgeResult(traces_deleted=traces, invocations_deleted=invocations)
