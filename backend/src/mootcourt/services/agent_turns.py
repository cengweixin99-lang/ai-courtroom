from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

import structlog
from pydantic import TypeAdapter, ValidationError

from mootcourt.agents.citation_anchors import build_evidence_citation_anchors
from mootcourt.agents.openai_compatible import AgentProviderError
from mootcourt.agents.providers import (
    CONTROLLED_CITATION_PROTOCOL,
    AgentProvider,
    AgentProviderRequest,
    AgentProviderResult,
    TextUpdateCallback,
)
from mootcourt.core.config import Settings
from mootcourt.core.observability import record_agent_turn
from mootcourt.core.trace_security import (
    protect_agent_trace_payloads,
    protected_trace_error_message,
)
from mootcourt.domain.courtroom import ActionRequest, CourtAction, CourtPhase, Role, validate_action
from mootcourt.repositories.agent_traces import AgentTraceRecord, SessionAgentUsage
from mootcourt.repositories.court_sessions import SessionEventRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import (
    AdvocateOutput,
    AgentContext,
    AgentEvidenceCitation,
    AgentLegalSourceContext,
    AgentOutput,
    AgentRole,
    AgentTraceStatus,
    AgentTraceView,
    AgentTurnError,
    AgentTurnRequest,
    AgentUsageView,
    ClaimType,
    DefendantOutput,
    WitnessOutput,
)
from mootcourt.schemas.runtime import (
    AgentTurnResponse,
    EvidenceChallengeDimension,
    ParticipantConsistencyStatus,
    ParticipantStatementTraceView,
    ProceduralRequestStatus,
    ProceduralRequestType,
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
AgentStreamCallback = Callable[[str, dict[str, object]], Awaitable[None]]
logger = structlog.get_logger(__name__)


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
    output_normalized: bool = False
    error_code: str | None = None
    error_message: str | None = None


async def execute_agent_turn(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: AgentTurnRequest,
    provider: AgentProvider,
    settings: Settings,
    stream_callback: AgentStreamCallback | None = None,
) -> AgentTurnResponse:
    effective_model = settings.role_model_map.get(request.actor_role.value, provider.model_name)
    logger.info(
        "agent_turn_started",
        session_id=session_id,
        actor_role=request.actor_role.value,
        action=request.action.value,
        provider=provider.provider_name,
        model=effective_model,
    )
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
    if stream_callback is not None:
        await stream_callback(
            "turn.started",
            {
                "actor_role": request.actor_role.value,
                "participant_id": request.participant_id,
            },
        )

    async def on_text_update(text: str) -> None:
        if stream_callback is not None:
            await stream_callback("turn.delta", {"text": text})

    preferred_model = settings.role_model_map.get(request.actor_role.value)
    invocation = await _invoke_provider(
        provider,
        context,
        request.instruction,
        on_text_update=on_text_update if stream_callback is not None else None,
        preferred_model=preferred_model,
    )
    if stream_callback is not None and invocation.provider_result is not None:
        await stream_callback("turn.validating", {})
    if invocation.output is None:
        return await _record_failed_turn(
            unit_of_work, session_id, request, context, provider, invocation, settings
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
            unit_of_work, session_id, request, context, provider, failed, settings
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
            unit_of_work, session_id, request, context, provider, failed, settings
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
            unit_of_work, session_id, request, context, provider, changed, settings
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
            unit_of_work, session_id, request, context, provider, failed, settings
        )

    content = _output_content(invocation.output)
    _validate_agent_request(locked.user_role, locked.status, CourtPhase(locked.phase), request)
    await _validate_payload(unit_of_work, locked.package_id, locked.id, request, content)
    event_evidence_ids = request.evidence_ids
    if request.action is CourtAction.CHALLENGE_EVIDENCE:
        # 质证范围是 Agent 可选择的证据集合；庭审记录只标记实际引用并发表意见的项目。
        event_evidence_ids = _advocate_cited_evidence_ids(invocation.output)

    provider_result = invocation.provider_result
    if provider_result is None:
        raise RuntimeError("successful agent invocation is missing provider metadata")
    trace_request_payload, trace_response_payload = protect_agent_trace_payloads(
        _trace_request(context, request.instruction),
        invocation.raw_output,
        mode=settings.agent_trace_payload_mode,
        hmac_key=settings.trace_redaction_hmac_key.get_secret_value(),
    )
    trace = await unit_of_work.agent_traces.add(
        session_id=session_id,
        actor_role=request.actor_role.value,
        participant_id=request.participant_id,
        provider=provider_result.provider,
        model=provider_result.model,
        status=AgentTraceStatus.SUCCEEDED.value,
        repair_count=invocation.repair_count,
        output_normalized=invocation.output_normalized,
        request_payload=trace_request_payload,
        response_payload=trace_response_payload,
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
    sequence_number = await unit_of_work.court_sessions.next_event_sequence(locked.id)
    if request.action is CourtAction.SUBMIT_EVIDENCE:
        unit_of_work.court_sessions.add_evidence_agenda_items(
            session_id=locked.id,
            phase=locked.phase,
            evidence_ids=request.evidence_ids,
            submitted_by=request.actor_role.value,
            responding_role=(
                AgentRole.DEFENSE.value
                if request.actor_role is AgentRole.PROSECUTION
                else AgentRole.PROSECUTION.value
            ),
            submission_event_sequence=sequence_number,
        )
    procedural_request = None
    if request.action is CourtAction.CHALLENGE_EVIDENCE:
        procedural_request = await unit_of_work.court_sessions.add_procedural_request(
            session_id=locked.id,
            request_type=ProceduralRequestType.EVIDENCE_CHALLENGE.value,
            raised_by=request.actor_role.value,
            event_sequence_number=sequence_number,
            target_event_sequence=None,
            evidence_ids=event_evidence_ids,
            challenge_dimensions=request.challenge_dimensions,
            content=content,
            status=ProceduralRequestStatus.RECORDED_FOR_EVALUATION.value,
        )
    event = await unit_of_work.court_sessions.add_event(
        session_id=locked.id,
        sequence_number=sequence_number,
        phase=locked.phase,
        actor_role=request.actor_role.value,
        action=request.action.value,
        payload={
            "target_id": request.target_id,
            "evidence_ids": event_evidence_ids,
            "content": content,
            "resulting_phase": locked.phase,
            "agent_role": request.actor_role.value,
            "participant_id": request.participant_id,
            "trace_id": trace.id,
            "agent_output": invocation.output.model_dump(mode="json"),
            "procedural_request_id": (
                procedural_request.id if procedural_request is not None else None
            ),
            "procedural_request_type": (
                procedural_request.request_type if procedural_request is not None else None
            ),
            "procedural_request_status": (
                procedural_request.status if procedural_request is not None else None
            ),
            "challenge_dimensions": request.challenge_dimensions,
        },
    )
    if isinstance(invocation.output, AdvocateOutput) and invocation.output.claims:
        # 与事件同事务写入 claim 索引；发问回合 claims 恒为空，自然不产生索引行。
        unit_of_work.court_sessions.add_role_claims(
            locked.id,
            event_sequence_number=sequence_number,
            phase=locked.phase,
            role=request.actor_role.value,
            claims=[
                {
                    "text": claim.text,
                    "claim_type": claim.claim_type.value,
                    "fact_ids": list(claim.fact_ids),
                }
                for claim in invocation.output.claims
            ],
        )
    if request.action is CourtAction.CHALLENGE_EVIDENCE:
        agenda_rows = await unit_of_work.court_sessions.evidence_agenda_for_update(
            locked.id, event_evidence_ids
        )
        await unit_of_work.court_sessions.record_evidence_agenda_response(
            agenda_rows,
            status="challenged",
            response_action=request.action.value,
            response_event_sequence=sequence_number,
            challenge_dimensions=request.challenge_dimensions,
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
    record_agent_turn(
        actor_role=request.actor_role.value,
        action=request.action.value,
        status=AgentTraceStatus.SUCCEEDED.value,
        error_code=None,
        provider=provider_result.provider,
        model=provider_result.model,
        latency_ms=invocation.latency_ms,
        input_tokens=provider_result.input_tokens,
        output_tokens=provider_result.output_tokens,
        repair_count=invocation.repair_count,
        output_normalized=invocation.output_normalized,
    )
    logger.info(
        "agent_turn_succeeded",
        session_id=session_id,
        trace_id=trace.id,
        actor_role=request.actor_role.value,
        action=request.action.value,
        provider=provider_result.provider,
        model=provider_result.model,
        latency_ms=invocation.latency_ms,
        input_tokens=provider_result.input_tokens,
        output_tokens=provider_result.output_tokens,
        repair_count=invocation.repair_count,
        output_normalized=invocation.output_normalized,
    )
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


async def get_agent_usage(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> AgentUsageView | None:
    if await unit_of_work.court_sessions.get(session_id) is None:
        return None
    usage = await unit_of_work.agent_traces.usage_for_session(session_id)
    return AgentUsageView(
        trace_count=usage.trace_count,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        latency_ms=usage.latency_ms,
        estimated_cost_cny=usage.estimated_cost_cny,
    )


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
                challenge_dimensions=[
                    EvidenceChallengeDimension(item) for item in request.challenge_dimensions
                ],
            ),
        )
    except SessionServiceError as exc:
        raise AgentTurnServiceError(exc.code, exc.message, exc.status_code) from exc


async def _invoke_provider(
    provider: AgentProvider,
    context: AgentContext,
    instruction: str | None,
    on_text_update: TextUpdateCallback | None = None,
    preferred_model: str | None = None,
) -> _InvocationResult:
    started = perf_counter()
    first_result: AgentProviderResult | None = None
    try:
        first_result = await provider.generate(
            AgentProviderRequest(
                context=context,
                instruction=instruction,
                on_text_update=on_text_update,
                preferred_model=preferred_model,
            )
        )
        first_payload, anchors_materialized, anchor_error = _materialize_advocate_citation_anchors(
            context,
            first_result.output,
            require_controlled=(first_result.citation_protocol == CONTROLLED_CITATION_PROTOCOL),
        )
        first_payload, payload_normalized = _normalize_explicit_refusal_payload(first_payload)
        if anchor_error is not None:
            repair_instruction = _business_repair_instruction(context, anchor_error)
        else:
            try:
                output = _OUTPUT_ADAPTER.validate_python(first_payload)
            except ValidationError as exc:
                repair_instruction = _repair_instruction(context, exc)
            else:
                output, output_normalized = _normalize_agent_output(context, output)
                output_normalized = anchors_materialized or payload_normalized or output_normalized
                output_error = _validate_output(context, output)
                if output_error is None:
                    return _InvocationResult(
                        output=output,
                        provider_result=first_result,
                        raw_output=first_result.output,
                        repair_count=0,
                        latency_ms=_elapsed_ms(started),
                        output_normalized=output_normalized,
                    )
                repair_instruction = _business_repair_instruction(context, output_error)

        # Schema 或确定性业务校验失败时只允许一次修复，防止无上限重试吞噬会话预算。
        if on_text_update is not None:
            await on_text_update("")
        repaired = await provider.generate(
            AgentProviderRequest(
                context=context,
                instruction=instruction,
                repair_instruction=repair_instruction,
                on_text_update=on_text_update,
                preferred_model=preferred_model,
            )
        )
        repaired_payload, anchors_materialized, anchor_error = (
            _materialize_advocate_citation_anchors(
                context,
                repaired.output,
                require_controlled=(repaired.citation_protocol == CONTROLLED_CITATION_PROTOCOL),
            )
        )
        repaired_payload, payload_normalized = _normalize_explicit_refusal_payload(repaired_payload)
        if anchor_error is not None:
            return _InvocationResult(
                output=None,
                provider_result=_merge_usage(first_result, repaired),
                raw_output=repaired.output,
                repair_count=1,
                latency_ms=_elapsed_ms(started),
                error_code="agent_output_forbidden",
                error_message=anchor_error,
            )
        try:
            output = _OUTPUT_ADAPTER.validate_python(repaired_payload)
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
        output, output_normalized = _normalize_agent_output(context, output)
        output_normalized = anchors_materialized or payload_normalized or output_normalized
        repaired_output_error = _validate_output(context, output)
        if repaired_output_error is not None:
            return _InvocationResult(
                output=None,
                provider_result=_merge_usage(first_result, repaired),
                raw_output=repaired.output,
                repair_count=1,
                latency_ms=_elapsed_ms(started),
                error_code="agent_output_forbidden",
                error_message=repaired_output_error,
            )
        return _InvocationResult(
            output=output,
            provider_result=_merge_usage(first_result, repaired),
            raw_output=repaired.output,
            repair_count=1,
            latency_ms=_elapsed_ms(started),
            output_normalized=output_normalized,
        )
    except AgentProviderError as exc:
        failed_result = AgentProviderResult(
            output={},
            provider=provider.provider_name,
            model=provider.model_name,
            input_tokens=exc.input_tokens,
            output_tokens=exc.output_tokens,
            estimated_input_tokens=exc.estimated_input_tokens,
            provider_request_count=exc.provider_request_count,
            estimated_cost_cny=exc.estimated_cost_cny,
        )
        return _InvocationResult(
            output=None,
            provider_result=(
                _merge_usage(first_result, failed_result)
                if first_result is not None
                else failed_result
            ),
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


def _repair_instruction(context: AgentContext, error: ValidationError) -> str:
    """把校验错误转换为值约束，避免模型把 Pydantic 类名误当作 kind。"""
    if context.actor_role in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
        required_values = (
            f'kind 必须严格为 "advocate"，speaker_role 必须严格为 '
            f'"{context.actor_role.value}"，requested_action 必须严格为 '
            f'"{context.action.value}"。'
        )
    elif context.actor_role is AgentRole.WITNESS:
        required_values = 'kind 必须严格为 "witness"。'
    else:
        required_values = 'kind 必须严格为 "defendant"，new_statement 必须严格为 false。'
    compact_errors = json.dumps(error.errors(include_url=False), ensure_ascii=False)
    return f"{required_values} 不得使用 Python 类名作为字段值。仅修复以下结构错误：{compact_errors}"


def _business_repair_instruction(context: AgentContext, error: str) -> str:
    """业务修复只能重排已有输出，不得借机引入新事实、证据或动作。"""
    if context.actor_role in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
        extra = (
            "先生成 claims，再把每个 claim.text 不作任何改写地逐字复制进 speech；"
            "每项 citation 只能选择动态 Schema 允许的单个 anchor_id，禁止输出、合并或改写 quote；"
            "fact_ids、动作和目标仍只能使用原任务允许值。"
        )
    else:
        extra = "引用锚点必须来自既有陈述并逐字出现在 answer；禁止补充新的案卷事实。"
    return f"上次输出未通过确定性业务校验：{error}。{extra}"


def _merge_usage(first: AgentProviderResult, second: AgentProviderResult) -> AgentProviderResult:
    return AgentProviderResult(
        output=second.output,
        provider=second.provider,
        model=second.model,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        estimated_input_tokens=(first.estimated_input_tokens + second.estimated_input_tokens),
        provider_request_count=first.provider_request_count + second.provider_request_count,
        estimated_cost_cny=first.estimated_cost_cny + second.estimated_cost_cny,
        citation_protocol=second.citation_protocol,
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
        if context.action is CourtAction.QUESTION_PARTICIPANT and output.claims:
            return "participant question output must not contain factual claims"
        evidence_by_id = {item.id: item for item in context.evidence}
        facts_by_id = {item.id: item for item in context.facts}
        visible_evidence_ids = set(evidence_by_id)
        task_evidence_ids = set(context.task.evidence_ids)
        allowed_evidence_ids = task_evidence_ids or visible_evidence_ids
        allowed_fact_ids = set(facts_by_id)
        if task_evidence_ids:
            allowed_fact_ids &= {
                fact_id
                for evidence in context.evidence
                if evidence.id in task_evidence_ids
                for fact_id in evidence.related_fact_ids
            }
        cited_ids = {
            evidence_citation.evidence_id
            for claim in output.claims
            for evidence_citation in claim.citations
        }
        if context.action is not CourtAction.QUESTION_PARTICIPANT and not output.claims:
            return "substantive advocate output must include structured claims"
        if cited_ids - allowed_evidence_ids:
            return "agent output cites evidence outside the current task scope"
        if context.action is CourtAction.SUBMIT_EVIDENCE and task_evidence_ids - cited_ids:
            return "agent output does not address every evidence item approved for this turn"
        normalized_speech = _normalize_grounding_text(output.speech)
        for claim in output.claims:
            if _normalize_grounding_text(claim.text) not in normalized_speech:
                return "structured claim text must appear in the rendered speech"
            if set(claim.fact_ids) - allowed_fact_ids:
                return "agent output binds a claim to a fact outside the current task scope"
            if not claim.citations:
                return "every advocate claim must include an evidence citation"
            claim_evidence_ids = {item.evidence_id for item in claim.citations}
            for evidence_citation in claim.citations:
                evidence = evidence_by_id.get(evidence_citation.evidence_id)
                if evidence is None:
                    return "agent output cites evidence outside the role-visible context"
                sources = [evidence.content, *evidence.reliability_notes]
                if not _quote_is_grounded(evidence_citation.quote, sources):
                    return "evidence citation quote is not present in the cited evidence"
            # 事实关系由案卷显式图谱决定，不能把“引用了真实原文”等同于“原文支持该事实”。
            for fact_id in claim.fact_ids:
                fact = facts_by_id[fact_id]
                related_evidence_ids = {
                    evidence_id
                    for evidence_id in claim_evidence_ids
                    if fact_id in evidence_by_id[evidence_id].related_fact_ids
                }
                allowed_relation_ids = set(fact.supporting_evidence_ids)
                if claim.claim_type is not ClaimType.SUPPORTED_FACT:
                    allowed_relation_ids.update(fact.contradicting_evidence_ids)
                if not related_evidence_ids.intersection(allowed_relation_ids):
                    return "claim fact is not connected to a cited evidence item"
        consistency_error = _claim_consistency_error(context, output)
        if consistency_error is not None:
            return consistency_error
        legal_error = _legal_citation_error(context, output)
        if legal_error is not None:
            return legal_error
        return None

    if isinstance(output, (WitnessOutput, DefendantOutput)):
        if context.participant is None:
            return "participant output has no participant context"
        allowed_statement_ids = {item.id for item in context.participant.statements}
        cited_ids = set(output.supported_by_statement_ids)
        if cited_ids - allowed_statement_ids:
            return "participant output cites an unavailable statement"
        citation_ids = [item.statement_id for item in output.citations]
        if len(citation_ids) != len(set(citation_ids)):
            return "participant output contains duplicate statement citations"
        if set(citation_ids) != cited_ids:
            return "participant citation anchors must match supported statement IDs"
        statements_by_id = {item.id: item for item in context.participant.statements}
        for statement_citation in output.citations:
            statement = statements_by_id.get(statement_citation.statement_id)
            if statement is None:
                return "participant output cites an unavailable statement"
            if not _quote_is_grounded(statement_citation.quote, [statement.text]):
                return "participant citation quote is not present in the prior statement"
            if not _quote_is_grounded(statement_citation.quote, [output.answer]):
                return "participant citation quote must appear in the answer"
        if (
            not cited_ids
            and output.refused_reason is None
            and not (isinstance(output, DefendantOutput) and output.new_statement)
        ):
            return "participant output must cite a statement or explicitly refuse"
    return None


# supported_fact 与 disputed_fact 构成同一角色在同一事实上的对立立场；
# inference 和 opinion 不做确定性矛盾判定，交给提示词约束。
_CONTRADICTING_CLAIM_TYPES = frozenset({ClaimType.SUPPORTED_FACT, ClaimType.DISPUTED_FACT})


def _claim_consistency_error(context: AgentContext, output: AdvocateOutput) -> str | None:
    """同一律师角色在同一事实上的 supported/disputed 立场反转视为确定性矛盾。"""
    if not context.role_public_claims:
        return None
    prior_positions: dict[ClaimType, set[str]] = {}
    for prior in context.role_public_claims:
        if prior.claim_type not in _CONTRADICTING_CLAIM_TYPES:
            continue
        positions = prior_positions.setdefault(prior.claim_type, set())
        for fact_id in prior.fact_ids:
            positions.add(fact_id)
    if not prior_positions:
        return None
    for claim in output.claims:
        if claim.claim_type not in _CONTRADICTING_CLAIM_TYPES:
            continue
        opposite = (
            ClaimType.DISPUTED_FACT
            if claim.claim_type is ClaimType.SUPPORTED_FACT
            else ClaimType.SUPPORTED_FACT
        )
        conflicting_facts = set(claim.fact_ids) & prior_positions.get(opposite, set())
        if conflicting_facts:
            facts_label = ",".join(sorted(conflicting_facts))
            return (
                f"claim contradicts the role's earlier public position on facts {facts_label} "
                f"({claim.claim_type.value} vs {opposite.value})"
            )
    return None


# 《法律名称》第X条（可选款、项）引用形式；条号支持中文数字与阿拉伯数字。
_LEGAL_CITATION_PATTERN = re.compile(
    r"《(?P<title>[^》]{2,40})》"
    r"(?P<article>第[〇零一二三四五六七八九十百千0-9]+条"
    r"(?:第[一二三四五六七八九十0-9]+款)?(?:第[一二三四五六七八九十0-9]+项)?)"
)
_TITLE_PARENTHETICAL_PATTERN = re.compile(r"（[^）]*）")


def _legal_citation_error(context: AgentContext, output: AdvocateOutput) -> str | None:
    """律师引用法条必须命中本案白名单法源，防止凭记忆捏造清单外条文。"""
    if not context.legal_sources:
        return None
    text = " ".join([output.speech, *(claim.text for claim in output.claims)])
    for match in _LEGAL_CITATION_PATTERN.finditer(text):
        cited_title = _normalize_instrument_title(match.group("title"))
        cited_article = match.group("article")
        if not any(
            _legal_source_matches(source, cited_title, cited_article)
            for source in context.legal_sources
        ):
            return (
                "legal citation is outside the case legal source whitelist: "
                f"《{match.group('title')}》{cited_article}"
            )
    return None


def _normalize_instrument_title(title: str) -> str:
    """去掉括号说明与空白，使《中华人民共和国刑法》能匹配带数据库备注的官方标题。"""
    return _TITLE_PARENTHETICAL_PATTERN.sub("", title).replace(" ", "")


def _legal_source_matches(
    source: AgentLegalSourceContext, cited_title: str, cited_article: str
) -> bool:
    instrument = _normalize_instrument_title(source.instrument_title)
    title_matches = bool(cited_title) and (cited_title in instrument or instrument in cited_title)
    if not title_matches:
        return False
    article = source.article_number.replace(" ", "")
    return article.startswith(cited_article) or cited_article.startswith(article)


def _materialize_advocate_citation_anchors(
    context: AgentContext,
    payload: dict[str, object],
    *,
    require_controlled: bool,
) -> tuple[dict[str, object], bool, str | None]:
    """把模型选择的锚点展开为现有领域模型使用的证据 ID 和权威原文。"""

    if payload.get("kind") != "advocate":
        return payload, False, None
    anchor_by_id = {item.anchor_id: item for item in build_evidence_citation_anchors(context)}
    claims = payload.get("claims")
    if not isinstance(claims, list):
        return payload, False, None

    materialized = False
    protocol_error = False
    normalized_claims: list[object] = []
    for claim in claims:
        if not isinstance(claim, dict):
            normalized_claims.append(claim)
            continue
        citations = claim.get("citations")
        if not isinstance(citations, list):
            normalized_claims.append(claim)
            continue
        normalized_citations: list[object] = []
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"anchor_id"}:
                protocol_error = protocol_error or require_controlled
                normalized_citations.append(citation)
                continue
            anchor_id = citation.get("anchor_id")
            anchor = anchor_by_id.get(anchor_id) if isinstance(anchor_id, str) else None
            if anchor is None:
                protocol_error = protocol_error or require_controlled
                normalized_citations.append(citation)
                continue
            normalized_citations.append({"evidence_id": anchor.evidence_id, "quote": anchor.quote})
            materialized = True
        normalized_claim = dict(claim)
        normalized_claim["citations"] = normalized_citations
        normalized_claims.append(normalized_claim)

    error = (
        "advocate citation must select exactly one valid controlled anchor_id"
        if protocol_error
        else None
    )
    if not materialized:
        return payload, False, error
    normalized_payload = dict(payload)
    normalized_payload["claims"] = normalized_claims
    return normalized_payload, True, error


def _normalize_agent_output(context: AgentContext, output: AgentOutput) -> tuple[AgentOutput, bool]:
    if isinstance(output, AdvocateOutput):
        return _normalize_advocate_output(context, output)
    return _normalize_participant_output(output)


def _normalize_advocate_output(
    context: AgentContext, output: AdvocateOutput
) -> tuple[AgentOutput, bool]:
    """只拆分能逐行核验的旧式拼接引文，不做任何语义改写或模糊匹配。"""

    evidence_by_id = {item.id: item for item in context.evidence}
    normalized_claims = []
    changed = False
    for claim in output.claims:
        normalized_citations: list[AgentEvidenceCitation] = []
        for citation in claim.citations:
            evidence = evidence_by_id.get(citation.evidence_id)
            if evidence is None:
                normalized_citations.append(citation)
                continue
            sources = [evidence.content, *evidence.reliability_notes]
            if _quote_is_grounded(citation.quote, sources):
                normalized_citations.append(citation)
                continue
            parts = list(
                dict.fromkeys(part.strip() for part in citation.quote.splitlines() if part.strip())
            )
            if len(parts) > 1 and all(
                len(part) >= 6 and _quote_is_grounded(part, sources) for part in parts
            ):
                normalized_citations.extend(
                    AgentEvidenceCitation(evidence_id=citation.evidence_id, quote=part)
                    for part in parts
                )
                changed = True
            else:
                normalized_citations.append(citation)
        normalized_claims.append(claim.model_copy(update={"citations": normalized_citations}))
    if not changed:
        return output, False
    return output.model_copy(update={"claims": normalized_claims}), True


def _normalize_participant_output(output: AgentOutput) -> tuple[AgentOutput, bool]:
    if not isinstance(output, (WitnessOutput, DefendantOutput)):
        return output, False
    quotes = list(
        dict.fromkeys(item.quote.strip() for item in output.citations if item.quote.strip())
    )
    if not quotes:
        return output, False
    if all(_quote_is_grounded(quote, [output.answer]) for quote in quotes):
        return output, False

    # 模型只负责选择可追溯陈述；Service 用已选原文渲染可见回答，避免为同义改写再次付费。
    rendered_answer = " ".join(quotes)
    if output.refused_reason is not None:
        rendered_answer += " 对超出既有陈述范围的问题，我无法回答。"
    return output.model_copy(update={"answer": rendered_answer}), True


def _advocate_cited_evidence_ids(output: AgentOutput) -> list[str]:
    if not isinstance(output, AdvocateOutput):
        return []
    return list(
        dict.fromkeys(
            citation.evidence_id for claim in output.claims for citation in claim.citations
        )
    )


def _normalize_explicit_refusal_payload(
    payload: dict[str, object],
) -> tuple[dict[str, object], bool]:
    if payload.get("kind") not in {"witness", "defendant"}:
        return payload, False
    answer = payload.get("answer")
    refused_reason = payload.get("refused_reason")
    if not isinstance(answer, str) or answer.strip():
        return payload, False
    if not isinstance(refused_reason, str) or not refused_reason.strip():
        return payload, False
    if payload.get("supported_by_statement_ids") != [] or payload.get("citations") != []:
        return payload, False

    # 非空 refused_reason 已是模型显式结构化拒答；用它渲染空回答，不从自然语言关键词推断语义。
    normalized = dict(payload)
    normalized["answer"] = refused_reason.strip()
    return normalized, True


def _normalize_grounding_text(value: str) -> str:
    return " ".join(value.split())


def _quote_is_grounded(quote: str, sources: list[str]) -> bool:
    normalized_quote = _normalize_grounding_text(quote)
    return bool(normalized_quote) and any(
        normalized_quote in _normalize_grounding_text(source) for source in sources
    )


async def _record_failed_turn(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: AgentTurnRequest,
    context: AgentContext,
    provider: AgentProvider,
    invocation: _InvocationResult,
    settings: Settings,
) -> AgentTurnResponse:
    result = invocation.provider_result
    trace_request_payload, trace_response_payload = protect_agent_trace_payloads(
        _trace_request(context, request.instruction),
        invocation.raw_output,
        mode=settings.agent_trace_payload_mode,
        hmac_key=settings.trace_redaction_hmac_key.get_secret_value(),
    )
    trace = await unit_of_work.agent_traces.add(
        session_id=session_id,
        actor_role=request.actor_role.value,
        participant_id=request.participant_id,
        provider=result.provider if result else provider.provider_name,
        model=result.model if result else provider.model_name,
        status=AgentTraceStatus.FAILED.value,
        repair_count=invocation.repair_count,
        output_normalized=invocation.output_normalized,
        request_payload=trace_request_payload,
        response_payload=trace_response_payload,
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        latency_ms=invocation.latency_ms,
        estimated_cost_cny=result.estimated_cost_cny if result else 0,
        error_code=invocation.error_code,
        error_message=protected_trace_error_message(
            invocation.error_message,
            mode=settings.agent_trace_payload_mode,
        ),
    )
    session_view = await get_session_view(unit_of_work, session_id)
    if session_view is None:
        raise RuntimeError("session disappeared while recording failed agent trace")
    provider_name = result.provider if result else provider.provider_name
    model_name = result.model if result else provider.model_name
    input_tokens = result.input_tokens if result else 0
    output_tokens = result.output_tokens if result else 0
    record_agent_turn(
        actor_role=request.actor_role.value,
        action=request.action.value,
        status=AgentTraceStatus.FAILED.value,
        error_code=invocation.error_code,
        provider=provider_name,
        model=model_name,
        latency_ms=invocation.latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        repair_count=invocation.repair_count,
        output_normalized=invocation.output_normalized,
    )
    logger.warning(
        "agent_turn_failed",
        session_id=session_id,
        trace_id=trace.id,
        actor_role=request.actor_role.value,
        action=request.action.value,
        provider=provider_name,
        model=model_name,
        error_code=invocation.error_code or "agent_turn_failed",
        latency_ms=invocation.latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        repair_count=invocation.repair_count,
    )
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
        output_normalized=trace.output_normalized,
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
    # 开发阶段只记录资源消耗；需要生产硬限额时通过显式配置恢复阻断。
    if not settings.session_budget_enforcement_enabled:
        return
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
    if not settings.session_budget_enforcement_enabled:
        return None
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
