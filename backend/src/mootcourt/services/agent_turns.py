from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from pydantic import TypeAdapter, ValidationError

from mootcourt.agents.openai_compatible import AgentProviderError
from mootcourt.agents.providers import (
    AgentProvider,
    AgentProviderRequest,
    AgentProviderResult,
)
from mootcourt.core.config import Settings
from mootcourt.domain.courtroom import ActionRequest, CourtAction, CourtPhase, Role, validate_action
from mootcourt.repositories.agent_traces import AgentTraceRecord, SessionAgentUsage
from mootcourt.repositories.court_sessions import SessionEventRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import (
    AdvocateOutput,
    AgentContext,
    AgentOutput,
    AgentRole,
    AgentTraceStatus,
    AgentTraceView,
    AgentTurnError,
    AgentTurnRequest,
    DefendantOutput,
    WitnessOutput,
)
from mootcourt.schemas.runtime import (
    AgentTurnResponse,
    ParticipantConsistencyStatus,
    ParticipantStatementTraceView,
    SessionActionRequest,
    SessionEventPayload,
    SessionEventView,
)
from mootcourt.services.agent_context import AgentContextError, build_agent_context
from mootcourt.services.court_sessions import (
    SessionServiceError,
    get_session_view,
    validate_action_payload,
)

_OUTPUT_ADAPTER: TypeAdapter[AgentOutput] = TypeAdapter(AgentOutput)


class AgentTurnServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class _InvocationResult:
    output: AgentOutput | None
    provider_result: AgentProviderResult | None
    raw_output: dict[str, object] | None
    repair_count: int
    latency_ms: int
    error_code: str | None = None
    error_message: str | None = None


async def execute_agent_turn(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: AgentTurnRequest,
    provider: AgentProvider,
    settings: Settings,
) -> AgentTurnResponse:
    session = await unit_of_work.court_sessions.get(session_id)
    if session is None:
        raise AgentTurnServiceError("session_not_found", "court session not found", 404)
    initial_turns = session.turns_used
    _validate_agent_request(session.user_role, session.status, CourtPhase(session.phase), request)
    if session.turns_used >= settings.session_max_turns:
        raise AgentTurnServiceError("turn_limit_reached", "session turn limit reached")
    usage = await unit_of_work.agent_traces.usage_for_session(session_id)
    _validate_budget_before_call(usage, settings)

    try:
        context = await build_agent_context(
            unit_of_work,
            session_id,
            session.package_id,
            CourtPhase(session.phase),
            request,
        )
    except AgentContextError as exc:
        raise AgentTurnServiceError(exc.code, exc.message, 422) from exc
    _validate_participant_type(context, request)

    # 在产生模型成本前完成所有可确定的证据、参与人和重复提交校验。
    await _validate_payload(unit_of_work, session.package_id, session.id, request, "待生成内容")
    invocation = await _invoke_provider(provider, context, request.instruction)
    if invocation.output is None:
        return await _record_failed_turn(
            unit_of_work, session_id, request, context, provider, invocation
        )

    budget_error = _budget_error_after_call(usage, invocation, settings)
    if budget_error is not None:
        code, message = budget_error
        failed = _InvocationResult(
            output=None,
            provider_result=invocation.provider_result,
            raw_output=invocation.raw_output,
            repair_count=invocation.repair_count,
            latency_ms=invocation.latency_ms,
            error_code=code,
            error_message=message,
        )
        return await _record_failed_turn(
            unit_of_work, session_id, request, context, provider, failed
        )

    output_error = _validate_output(context, invocation.output)
    if output_error is not None:
        failed = _InvocationResult(
            output=None,
            provider_result=invocation.provider_result,
            raw_output=invocation.raw_output,
            repair_count=invocation.repair_count,
            latency_ms=invocation.latency_ms,
            error_code="agent_output_forbidden",
            error_message=output_error,
        )
        return await _record_failed_turn(
            unit_of_work, session_id, request, context, provider, failed
        )

    # 模型调用期间不持有行锁；落库前重新加锁并验证阶段未变化，避免过期输出污染庭审记录。
    locked = await unit_of_work.court_sessions.get_for_update(session_id)
    if locked is None:
        raise AgentTurnServiceError("session_not_found", "court session not found", 404)
    if (
        locked.phase != context.phase.value
        or locked.status != "active"
        or locked.turns_used != initial_turns
    ):
        changed = _InvocationResult(
            output=None,
            provider_result=invocation.provider_result,
            raw_output=invocation.raw_output,
            repair_count=invocation.repair_count,
            latency_ms=invocation.latency_ms,
            error_code="session_state_changed",
            error_message="session state changed while the agent was running",
        )
        return await _record_failed_turn(
            unit_of_work, session_id, request, context, provider, changed
        )
    if locked.turns_used >= settings.session_max_turns:
        raise AgentTurnServiceError("turn_limit_reached", "session turn limit reached")

    latest_usage = await unit_of_work.agent_traces.usage_for_session(session_id, lock_rows=True)
    latest_budget_error = _budget_error_after_call(latest_usage, invocation, settings)
    if latest_budget_error is not None:
        code, message = latest_budget_error
        failed = _InvocationResult(
            output=None,
            provider_result=invocation.provider_result,
            raw_output=invocation.raw_output,
            repair_count=invocation.repair_count,
            latency_ms=invocation.latency_ms,
            error_code=code,
            error_message=message,
        )
        return await _record_failed_turn(
            unit_of_work, session_id, request, context, provider, failed
        )

    content = _output_content(invocation.output)
    _validate_agent_request(locked.user_role, locked.status, CourtPhase(locked.phase), request)
    await _validate_payload(unit_of_work, locked.package_id, locked.id, request, content)

    provider_result = invocation.provider_result
    if provider_result is None:
        raise RuntimeError("successful agent invocation is missing provider metadata")
    trace = await unit_of_work.agent_traces.add(
        session_id=session_id,
        actor_role=request.actor_role.value,
        participant_id=request.participant_id,
        provider=provider_result.provider,
        model=provider_result.model,
        status=AgentTraceStatus.SUCCEEDED.value,
        repair_count=invocation.repair_count,
        request_payload=_trace_request(context, request.instruction),
        response_payload=invocation.raw_output,
        input_tokens=provider_result.input_tokens,
        output_tokens=provider_result.output_tokens,
        latency_ms=invocation.latency_ms,
        estimated_cost_cny=provider_result.estimated_cost_cny,
    )
    if request.action is CourtAction.SUBMIT_EVIDENCE:
        unit_of_work.court_sessions.add_evidence_submissions(
            locked.id, request.evidence_ids, request.actor_role.value
        )
    locked.turns_used += 1
    event = await unit_of_work.court_sessions.add_event(
        session_id=locked.id,
        sequence_number=await unit_of_work.court_sessions.next_event_sequence(locked.id),
        phase=locked.phase,
        actor_role=request.actor_role.value,
        action=request.action.value,
        payload={
            "target_id": request.target_id,
            "evidence_ids": request.evidence_ids,
            "content": content,
            "resulting_phase": locked.phase,
            "agent_role": request.actor_role.value,
            "participant_id": request.participant_id,
            "trace_id": trace.id,
            "agent_output": invocation.output.model_dump(mode="json"),
        },
    )
    if isinstance(invocation.output, (WitnessOutput, DefendantOutput)):
        if context.participant is None or request.participant_id is None:
            raise RuntimeError("validated participant output is missing participant context")
        statements_by_id = {item.id: item for item in context.participant.statements}
        related_fact_ids = sorted(
            {
                fact_id
                for statement_id in invocation.output.supported_by_statement_ids
                for fact_id in statements_by_id[statement_id].related_fact_ids
            }
        )
        # 一致性状态只依据案卷陈述引用和明确拒答分类，不用字符串相似度冒充语义判断。
        consistency_status = _participant_consistency_status(invocation.output)
        await unit_of_work.court_sessions.add_participant_statement_trace(
            session_id=locked.id,
            participant_id=request.participant_id,
            actor_role=request.actor_role.value,
            event_sequence_number=event.sequence_number,
            answer=invocation.output.answer,
            supported_statement_ids=invocation.output.supported_by_statement_ids,
            related_fact_ids=related_fact_ids,
            consistency_status=consistency_status.value,
            new_statement=(
                invocation.output.new_statement
                if isinstance(invocation.output, DefendantOutput)
                else False
            ),
            refused_reason=invocation.output.refused_reason,
        )
    await unit_of_work.court_sessions.flush_session(locked)
    session_view = await get_session_view(unit_of_work, locked.id)
    if session_view is None:
        raise RuntimeError("session disappeared after agent event flush")
    return AgentTurnResponse(
        status=AgentTraceStatus.SUCCEEDED,
        session=session_view,
        event=_event_view(event),
        output=invocation.output,
        trace=_trace_view(trace),
    )


async def list_agent_traces(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> list[AgentTraceView] | None:
    if await unit_of_work.court_sessions.get(session_id) is None:
        return None
    return [
        _trace_view(item) for item in await unit_of_work.agent_traces.list_for_session(session_id)
    ]


async def list_participant_statement_traces(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> list[ParticipantStatementTraceView] | None:
    if await unit_of_work.court_sessions.get(session_id) is None:
        return None
    rows = await unit_of_work.court_sessions.list_participant_statement_traces(session_id)
    return [
        ParticipantStatementTraceView.model_validate(
            {
                "id": item.id,
                "session_id": item.session_id,
                "participant_id": item.participant_id,
                "actor_role": item.actor_role,
                "event_sequence_number": item.event_sequence_number,
                "answer": item.answer,
                "supported_statement_ids": item.supported_statement_ids,
                "related_fact_ids": item.related_fact_ids,
                "consistency_status": item.consistency_status,
                "new_statement": item.new_statement,
                "refused_reason": item.refused_reason,
                "review_status": item.review_status,
                "review_reason": item.review_reason,
                "reviewed_at": item.reviewed_at,
                "review_event_sequence": item.review_event_sequence,
                "created_at": item.created_at,
            }
        )
        for item in rows
    ]


def _participant_consistency_status(
    output: WitnessOutput | DefendantOutput,
) -> ParticipantConsistencyStatus:
    if isinstance(output, DefendantOutput) and output.new_statement:
        return ParticipantConsistencyStatus.NEW_STATEMENT_PENDING_REVIEW
    if output.supported_by_statement_ids:
        return ParticipantConsistencyStatus.SUPPORTED_BY_PRIOR_STATEMENT
    if output.refused_reason is not None:
        return ParticipantConsistencyStatus.EXPLICIT_REFUSAL
    return ParticipantConsistencyStatus.UNSUPPORTED


def _validate_agent_request(
    user_role: str,
    session_status: str,
    phase: CourtPhase,
    request: AgentTurnRequest,
) -> None:
    if session_status != "active":
        raise AgentTurnServiceError("session_closed", "court session is already completed")
    if request.actor_role.value == user_role:
        raise AgentTurnServiceError(
            "user_controlled_role",
            "the selected user role cannot also be controlled by an agent",
        )
    if request.actor_role in {AgentRole.WITNESS, AgentRole.DEFENDANT}:
        if request.participant_id is None:
            raise AgentTurnServiceError(
                "participant_required", "participant_id is required for this agent role", 422
            )
    elif request.participant_id is not None:
        raise AgentTurnServiceError(
            "participant_not_allowed", "advocate agents cannot bind a participant_id", 422
        )

    decision = validate_action(
        phase,
        ActionRequest(
            role=Role(request.actor_role.value),
            action=request.action,
            target_id=request.target_id,
            evidence_ids=request.evidence_ids,
        ),
    )
    if not decision.allowed:
        raise AgentTurnServiceError("action_not_allowed", decision.reason or "action not allowed")


def _validate_participant_type(context: AgentContext, request: AgentTurnRequest) -> None:
    participant = context.participant
    if request.actor_role is AgentRole.WITNESS and (
        participant is None or participant.participant_type != "witness"
    ):
        raise AgentTurnServiceError(
            "participant_role_mismatch", "participant is not a witness", 422
        )
    if request.actor_role is AgentRole.DEFENDANT and (
        participant is None or participant.participant_type != "defendant"
    ):
        raise AgentTurnServiceError(
            "participant_role_mismatch", "participant is not the defendant", 422
        )


async def _validate_payload(
    unit_of_work: SqlAlchemyUnitOfWork,
    package_id: int,
    session_id: str,
    request: AgentTurnRequest,
    content: str,
) -> None:
    try:
        await validate_action_payload(
            unit_of_work,
            package_id,
            session_id,
            Role(request.actor_role.value),
            SessionActionRequest(
                action=request.action,
                target_id=request.target_id,
                evidence_ids=request.evidence_ids,
                content=content,
            ),
        )
    except SessionServiceError as exc:
        raise AgentTurnServiceError(exc.code, exc.message, exc.status_code) from exc


async def _invoke_provider(
    provider: AgentProvider,
    context: AgentContext,
    instruction: str | None,
) -> _InvocationResult:
    started = perf_counter()
    first_result: AgentProviderResult | None = None
    try:
        first_result = await provider.generate(
            AgentProviderRequest(context=context, instruction=instruction)
        )
        try:
            output = _OUTPUT_ADAPTER.validate_python(first_result.output)
            return _InvocationResult(
                output=output,
                provider_result=first_result,
                raw_output=first_result.output,
                repair_count=0,
                latency_ms=_elapsed_ms(started),
            )
        except ValidationError as exc:
            # 只允许一次格式修复，防止无上限重试吞噬会话预算。
            repaired = await provider.generate(
                AgentProviderRequest(
                    context=context,
                    instruction=instruction,
                    repair_instruction=f"请严格按输出 Schema 修复以下错误：{exc}",
                )
            )
            try:
                output = _OUTPUT_ADAPTER.validate_python(repaired.output)
            except ValidationError as repaired_exc:
                return _InvocationResult(
                    output=None,
                    provider_result=_merge_usage(first_result, repaired),
                    raw_output=repaired.output,
                    repair_count=1,
                    latency_ms=_elapsed_ms(started),
                    error_code="agent_output_invalid",
                    error_message=str(repaired_exc),
                )
            return _InvocationResult(
                output=output,
                provider_result=_merge_usage(first_result, repaired),
                raw_output=repaired.output,
                repair_count=1,
                latency_ms=_elapsed_ms(started),
            )
    except AgentProviderError as exc:
        return _InvocationResult(
            output=None,
            provider_result=first_result,
            raw_output=first_result.output if first_result else None,
            repair_count=1 if first_result else 0,
            latency_ms=_elapsed_ms(started),
            error_code=exc.code,
            error_message=exc.message,
        )
    except Exception as exc:
        return _InvocationResult(
            output=None,
            provider_result=first_result,
            raw_output=first_result.output if first_result else None,
            repair_count=1 if first_result else 0,
            latency_ms=_elapsed_ms(started),
            error_code="agent_provider_failed",
            error_message=str(exc),
        )


def _merge_usage(first: AgentProviderResult, second: AgentProviderResult) -> AgentProviderResult:
    return AgentProviderResult(
        output=second.output,
        provider=second.provider,
        model=second.model,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        estimated_cost_cny=first.estimated_cost_cny + second.estimated_cost_cny,
    )


def _validate_output(context: AgentContext, output: AgentOutput) -> str | None:
    if context.actor_role in {AgentRole.PROSECUTION, AgentRole.DEFENSE} and not isinstance(
        output, AdvocateOutput
    ):
        return "advocate invocation returned a participant output"
    if context.actor_role is AgentRole.WITNESS and not isinstance(output, WitnessOutput):
        return "witness invocation returned an incompatible output kind"
    if context.actor_role is AgentRole.DEFENDANT and not isinstance(output, DefendantOutput):
        return "defendant invocation returned an incompatible output kind"

    if isinstance(output, AdvocateOutput):
        if output.speaker_role.value != context.actor_role.value:
            return "agent output speaker_role does not match the invoked role"
        if output.requested_action is not context.action:
            return "agent requested_action does not match the approved action"
        expected_target = context.participant.id if context.participant is not None else None
        if output.target_id != expected_target:
            return "agent output target_id does not match the approved target"
        visible_evidence_ids = {item.id for item in context.evidence}
        cited_ids = {item for claim in output.claims for item in claim.evidence_ids}
        if cited_ids - visible_evidence_ids:
            return "agent output cites evidence outside the role-visible context"
        return None

    if isinstance(output, (WitnessOutput, DefendantOutput)):
        if context.participant is None:
            return "participant output has no participant context"
        allowed_statement_ids = {item.id for item in context.participant.statements}
        cited_ids = set(output.supported_by_statement_ids)
        if cited_ids - allowed_statement_ids:
            return "participant output cites an unavailable statement"
        if (
            not cited_ids
            and output.refused_reason is None
            and not (isinstance(output, DefendantOutput) and output.new_statement)
        ):
            return "participant output must cite a statement or explicitly refuse"
    return None


async def _record_failed_turn(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: AgentTurnRequest,
    context: AgentContext,
    provider: AgentProvider,
    invocation: _InvocationResult,
) -> AgentTurnResponse:
    result = invocation.provider_result
    trace = await unit_of_work.agent_traces.add(
        session_id=session_id,
        actor_role=request.actor_role.value,
        participant_id=request.participant_id,
        provider=result.provider if result else provider.provider_name,
        model=result.model if result else provider.model_name,
        status=AgentTraceStatus.FAILED.value,
        repair_count=invocation.repair_count,
        request_payload=_trace_request(context, request.instruction),
        response_payload=invocation.raw_output,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        latency_ms=invocation.latency_ms,
        estimated_cost_cny=result.estimated_cost_cny if result else 0,
        error_code=invocation.error_code,
        error_message=invocation.error_message,
    )
    session_view = await get_session_view(unit_of_work, session_id)
    if session_view is None:
        raise RuntimeError("session disappeared while recording failed agent trace")
    return AgentTurnResponse(
        status=AgentTraceStatus.FAILED,
        session=session_view,
        event=None,
        output=None,
        trace=_trace_view(trace),
        error=AgentTurnError(
            code=invocation.error_code or "agent_turn_failed",
            message=invocation.error_message or "agent turn failed",
        ),
    )


def _trace_request(context: AgentContext, instruction: str | None) -> dict[str, object]:
    return {
        "context": context.model_dump(mode="json"),
        "instruction": instruction,
    }


def _output_content(output: AgentOutput) -> str:
    return output.speech if isinstance(output, AdvocateOutput) else output.answer


def _trace_view(trace: AgentTraceRecord) -> AgentTraceView:
    return AgentTraceView(
        trace_id=trace.id,
        session_id=trace.session_id,
        actor_role=AgentRole(trace.actor_role),
        participant_id=trace.participant_id,
        provider=trace.provider,
        model=trace.model,
        status=AgentTraceStatus(trace.status),
        repair_count=trace.repair_count,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        latency_ms=trace.latency_ms,
        estimated_cost_cny=trace.estimated_cost_cny,
        error_code=trace.error_code,
        error_message=trace.error_message,
        created_at=trace.created_at,
    )


def _event_view(event: SessionEventRecord) -> SessionEventView:
    # Repository 返回 ORM Record；在 Service 边界统一转换，避免 API 接触持久化模型。
    return SessionEventView(
        sequence_number=event.sequence_number,
        phase=CourtPhase(event.phase),
        actor_role=Role(event.actor_role),
        action=CourtAction(event.action),
        payload=SessionEventPayload.model_validate(event.payload),
        created_at=event.created_at,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1_000))


def _validate_budget_before_call(
    usage: SessionAgentUsage,
    settings: Settings,
) -> None:
    if usage.total_tokens >= settings.session_max_tokens:
        raise AgentTurnServiceError(
            "session_token_budget_exceeded", "session token budget is exhausted", 429
        )
    if usage.estimated_cost_cny >= settings.session_max_cost_cny:
        raise AgentTurnServiceError(
            "session_cost_budget_exceeded", "session cost budget is exhausted", 429
        )
    if usage.latency_ms >= settings.session_max_seconds * 1_000:
        raise AgentTurnServiceError(
            "session_time_budget_exceeded", "session time budget is exhausted", 429
        )


def _budget_error_after_call(
    usage: SessionAgentUsage,
    invocation: _InvocationResult,
    settings: Settings,
) -> tuple[str, str] | None:
    result = invocation.provider_result
    if result is None:
        return None
    total_tokens = usage.total_tokens + result.input_tokens + result.output_tokens
    if total_tokens > settings.session_max_tokens:
        return "session_token_budget_exceeded", "model call exceeded the session token budget"
    total_cost = usage.estimated_cost_cny + result.estimated_cost_cny
    if total_cost > settings.session_max_cost_cny:
        return "session_cost_budget_exceeded", "model call exceeded the session cost budget"
    # 时间预算统计模型累计调用耗时；页面闲置、电脑休眠和隔日恢复不会消耗该预算。
    if usage.latency_ms + invocation.latency_ms > settings.session_max_seconds * 1_000:
        return "session_time_budget_exceeded", "model call exceeded the session time budget"
    return None
