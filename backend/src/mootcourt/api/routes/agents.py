import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import MutableHeaders

from mootcourt.api.dependencies import (
    RuntimeAgentProvider,
    RuntimeDiagnosticsAccess,
    RuntimeUnitOfWork,
    StreamingUnitOfWork,
)
from mootcourt.core.config import Settings, get_settings
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import (
    AgentTraceStatus,
    AgentTraceView,
    AgentTurnRequest,
    AgentUsageView,
)
from mootcourt.schemas.runtime import (
    AgentTurnResponse,
    AutoStepResponse,
    ParticipantStatementTraceView,
)
from mootcourt.services.agent_invocations import (
    AgentInvocationError,
    AgentInvocationLease,
    abandon_agent_invocation,
    acquire_agent_invocation,
    complete_agent_invocation,
)
from mootcourt.services.agent_turns import (
    AgentTurnServiceError,
    execute_agent_turn,
    get_agent_usage,
    list_agent_traces,
    list_participant_statement_traces,
)
from mootcourt.services.court_orchestrator import CourtOrchestratorError, run_automatic_step

router = APIRouter(prefix="/sessions", tags=["agents"])
AppSettings = Annotated[Settings, Depends(get_settings)]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="客户端生成的逻辑请求唯一键；重连时复用，执行下一步时更换",
    ),
]


@router.post(
    "/{session_id}/auto-step",
    response_model=AutoStepResponse,
    operation_id="run_automatic_court_step",
    summary="自动执行下一庭审步骤",
    response_description="执行一个非用户角色回合、推进一个阶段，或返回用户暂停点",
    responses={
        404: {"description": "庭审会话不存在"},
        409: {"description": "庭审状态不允许继续自动执行"},
        429: {"description": "会话资源预算耗尽"},
        503: {"description": "真实模型 Provider 未配置"},
    },
)
async def run_auto_step(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
    provider: RuntimeAgentProvider,
    settings: AppSettings,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> AutoStepResponse:
    """控制器单步执行自动流程；每次最多产生一个事件，便于提交、重试和审计。"""
    lease = await _begin_invocation(
        unit_of_work,
        session_id,
        "auto_step",
        idempotency_key,
        {},
        settings,
    )
    _set_idempotency_headers(response.headers, lease)
    if lease.replayed_payload is not None:
        return AutoStepResponse.model_validate(lease.replayed_payload)
    try:
        result = await run_automatic_step(unit_of_work, session_id, provider, settings)
        await complete_agent_invocation(
            unit_of_work,
            session_id,
            lease,
            result.model_dump(mode="json"),
            _idempotency_encryption_key(settings),
        )
        return result
    except CourtOrchestratorError as exc:
        await _abandon_and_commit(unit_of_work, session_id, lease, exc.code)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except AgentInvocationError as exc:
        await _abandon_and_commit(unit_of_work, session_id, lease, exc.code)
        raise _invocation_http_error(exc) from exc
    except Exception:
        await _abandon_and_commit(unit_of_work, session_id, lease, "automatic_step_unhandled_error")
        raise


@router.post(
    "/{session_id}/auto-step/stream",
    operation_id="stream_automatic_court_step",
    summary="流式执行下一庭审步骤",
    response_class=EventSourceResponse,
    responses={
        404: {"description": "庭审会话不存在"},
        409: {"description": "庭审状态不允许继续自动执行"},
        429: {"description": "会话资源预算耗尽"},
        503: {"description": "真实模型 Provider 未配置"},
    },
)
async def stream_auto_step(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: StreamingUnitOfWork,
    provider: RuntimeAgentProvider,
    settings: AppSettings,
    idempotency_key: IdempotencyKey = None,
) -> EventSourceResponse:
    """以 SSE 推送临时发言快照；正式事件仍在严格校验并提交后才发送完成通知。"""
    lease = await _begin_invocation(
        unit_of_work,
        session_id,
        "auto_step",
        idempotency_key,
        {},
        settings,
    )
    response_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Idempotency-Key": lease.idempotency_key,
        "Idempotency-Replayed": "true" if lease.replayed else "false",
    }

    replayed_payload = lease.replayed_payload
    if replayed_payload is not None:

        async def replay_events() -> AsyncIterator[dict[str, str]]:
            yield _sse_event("step.started", {"session_id": session_id})
            yield _sse_event("step.completed", replayed_payload)

        return EventSourceResponse(replay_events(), ping=15, headers=response_headers)

    async def events() -> AsyncIterator[dict[str, str]]:
        queue: asyncio.Queue[tuple[str, dict[str, object]] | None] = asyncio.Queue()

        async def emit(event: str, payload: dict[str, object]) -> None:
            await queue.put((event, payload))

        async def worker() -> None:
            try:
                result = await run_automatic_step(
                    unit_of_work,
                    session_id,
                    provider,
                    settings,
                    stream_callback=emit,
                )
                await complete_agent_invocation(
                    unit_of_work,
                    session_id,
                    lease,
                    result.model_dump(mode="json"),
                    _idempotency_encryption_key(settings),
                )
                # 浏览器收到完成事件后会立即刷新，必须确保此时事务已经可见。
                await unit_of_work.commit()
                await emit("step.completed", result.model_dump(mode="json"))
            except asyncio.CancelledError:
                # 浏览器刷新会取消 SSE 请求；必须释放租约，否则同一会话会被锁到租约自然过期。
                await _abandon_and_commit(
                    unit_of_work, session_id, lease, "stream_client_disconnected"
                )
                await emit(
                    "step.failed",
                    {
                        "code": "stream_client_disconnected",
                        "message": "streaming court step was interrupted by client disconnect",
                        "status": 409,
                    },
                )
            except CourtOrchestratorError as exc:
                await _abandon_and_commit(unit_of_work, session_id, lease, exc.code)
                await emit(
                    "step.failed",
                    {"code": exc.code, "message": exc.message, "status": exc.status_code},
                )
            except AgentInvocationError as exc:
                await _abandon_and_commit(unit_of_work, session_id, lease, exc.code)
                await emit(
                    "step.failed",
                    {"code": exc.code, "message": exc.message, "status": exc.status_code},
                )
            except Exception:
                await _abandon_and_commit(unit_of_work, session_id, lease, "stream_step_failed")
                await emit(
                    "step.failed",
                    {"code": "stream_step_failed", "message": "automatic court step failed"},
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            yield _sse_event("step.started", {"session_id": session_id})
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield _sse_event(event, payload)
        finally:
            if not task.done():
                # 客户端断开不取消已计费调用；等待 worker 提交后，同一幂等键即可回放结果。
                with suppress(asyncio.CancelledError):
                    await task

    return EventSourceResponse(
        events(),
        ping=15,
        headers=response_headers,
    )


def _sse_event(event: str, payload: dict[str, object]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


@router.post(
    "/{session_id}/agent-turns",
    response_model=AgentTurnResponse,
    operation_id="execute_agent_turn",
    summary="执行一次受控 Agent 回合",
    response_description="Agent 输出、庭审事件、会话状态和调用 Trace",
    responses={
        403: {"description": "Agent 输出引用当前角色无权访问的材料"},
        404: {"description": "庭审会话或参与人不存在"},
        409: {"description": "角色、动作或会话状态不允许本次调用"},
        429: {"description": "会话 Token、成本或时间预算已耗尽"},
        422: {"description": "请求参数或参与人类型无效"},
        502: {"description": "模型调用或结构化输出校验失败；失败 Trace 已保存"},
        503: {"description": "真实模型 Provider 未正确配置"},
    },
)
async def run_agent_turn(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    request: AgentTurnRequest,
    response: Response,
    unit_of_work: RuntimeUnitOfWork,
    provider: RuntimeAgentProvider,
    settings: AppSettings,
    idempotency_key: IdempotencyKey = None,
) -> AgentTurnResponse:
    """执行由状态机约束的单次 Agent 调用。

    客户端可以指定希望调用的系统角色和动作，但 Service 会根据会话用户角色、当前阶段、
    参与人类型和证据权限重新验证。失败调用保存 Trace，但不会写入庭审事件。
    """
    lease = await _begin_invocation(
        unit_of_work,
        session_id,
        "agent_turn",
        idempotency_key,
        request.model_dump(mode="json"),
        settings,
    )
    _set_idempotency_headers(response.headers, lease)
    if lease.replayed_payload is not None:
        result = AgentTurnResponse.model_validate(lease.replayed_payload)
    else:
        try:
            result = await execute_agent_turn(unit_of_work, session_id, request, provider, settings)
            await complete_agent_invocation(
                unit_of_work,
                session_id,
                lease,
                result.model_dump(mode="json"),
                _idempotency_encryption_key(settings),
            )
        except AgentTurnServiceError as exc:
            await _abandon_and_commit(unit_of_work, session_id, lease, exc.code)
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except AgentInvocationError as exc:
            await _abandon_and_commit(unit_of_work, session_id, lease, exc.code)
            raise _invocation_http_error(exc) from exc
        except Exception:
            await _abandon_and_commit(unit_of_work, session_id, lease, "agent_turn_unhandled_error")
            raise
    if result.status is AgentTraceStatus.FAILED:
        # 不抛异常，使依赖层可以提交失败 Trace；预算失败使用 429，其余上游失败使用 502。
        error_code = result.error.code if result.error is not None else ""
        response.status_code = _failed_agent_status(error_code)
    return result


async def _begin_invocation(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    operation: str,
    idempotency_key: str | None,
    request_payload: dict[str, object],
    settings: Settings,
) -> AgentInvocationLease:
    try:
        lease = await acquire_agent_invocation(
            unit_of_work,
            session_id,
            operation,
            idempotency_key,
            request_payload,
            settings.agent_invocation_lease_seconds,
            _idempotency_encryption_key(settings),
        )
        await unit_of_work.commit()
        return lease
    except AgentInvocationError as exc:
        await unit_of_work.rollback()
        raise _invocation_http_error(exc) from exc


async def _abandon_and_commit(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    lease: AgentInvocationLease,
    error_code: str,
) -> None:
    await unit_of_work.rollback()
    await abandon_agent_invocation(unit_of_work, session_id, lease, error_code)
    await unit_of_work.commit()


def _invocation_http_error(exc: AgentInvocationError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def _set_idempotency_headers(headers: MutableHeaders, lease: AgentInvocationLease) -> None:
    headers["Idempotency-Key"] = lease.idempotency_key
    headers["Idempotency-Replayed"] = "true" if lease.replayed else "false"


def _idempotency_encryption_key(settings: Settings) -> str:
    """生产环境未单独配置时复用诊断密钥，避免回放结果落成明文。"""
    configured = settings.idempotency_encryption_key.get_secret_value()
    if configured:
        return configured
    return (
        settings.diagnostics_api_key.get_secret_value()
        if settings.app_env.lower() == "production"
        else ""
    )


def _failed_agent_status(error_code: str) -> int:
    if error_code in {"agent_provider_overloaded", "agent_provider_rate_limited"}:
        return status.HTTP_429_TOO_MANY_REQUESTS
    if error_code in {
        "agent_provider_circuit_open",
        "agent_provider_draining",
        "agent_provider_guard_unavailable",
        "agent_provider_timeout",
        "agent_provider_unavailable",
    }:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    if error_code == "agent_context_too_large":
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    if error_code.startswith("session_") and error_code.endswith("_budget_exceeded"):
        return status.HTTP_429_TOO_MANY_REQUESTS
    return status.HTTP_502_BAD_GATEWAY


@router.get(
    "/{session_id}/traces",
    response_model=list[AgentTraceView],
    operation_id="list_agent_traces",
    summary="获取会话 Agent 调用 Trace",
    response_description="按创建时间排列的 Agent 调用诊断元数据",
    responses={
        401: {"description": "生产环境需要诊断访问凭据"},
        404: {"description": "庭审会话不存在"},
    },
)
async def get_agent_traces(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
    _: RuntimeDiagnosticsAccess,
) -> list[AgentTraceView]:
    """返回调用状态、模型、Token、延迟、成本和错误，不暴露完整提示词快照。"""
    traces = await list_agent_traces(unit_of_work, session_id)
    if traces is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return traces


@router.get(
    "/{session_id}/usage",
    response_model=AgentUsageView,
    operation_id="get_agent_usage",
    summary="获取会话累计模型用量",
    response_description="累计输入与输出 Token、调用耗时及预估费用；默认仅记录，不中断庭审",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_session_agent_usage(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> AgentUsageView:
    """聚合成功和失败的 Agent Trace，用于前端持续展示真实模型消耗。"""
    usage = await get_agent_usage(unit_of_work, session_id)
    if usage is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return usage


@router.get(
    "/{session_id}/participant-statement-traces",
    response_model=list[ParticipantStatementTraceView],
    operation_id="list_participant_statement_traces",
    summary="获取参与人陈述一致性留痕",
    response_description="证人或被告人回答引用的既有陈述、关联事实及确定性分类",
    responses={404: {"description": "庭审会话不存在"}},
)
async def get_participant_statement_traces(
    session_id: Annotated[str, Path(description="庭审会话唯一标识")],
    unit_of_work: RuntimeUnitOfWork,
) -> list[ParticipantStatementTraceView]:
    """返回可审计的一致性记录；系统不以字符串规则推断语义矛盾。"""
    traces = await list_participant_statement_traces(unit_of_work, session_id)
    if traces is None:
        raise HTTPException(status_code=404, detail={"code": "session_not_found"})
    return traces
