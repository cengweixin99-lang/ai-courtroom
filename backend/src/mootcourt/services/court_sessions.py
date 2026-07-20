from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from mootcourt.core.config import Settings
from mootcourt.domain.courtroom import (
    ActionRequest,
    CourtAction,
    CourtPhase,
    Role,
    allowed_actions,
    next_phase,
    validate_action,
)
from mootcourt.repositories.court_sessions import ProceduralRequestRecord, SessionEventRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.runtime import (
    EvidenceFactSummaryView,
    EvidenceFactSupportStatus,
    EvidenceStatusView,
    EvidenceSubmissionStatus,
    ProceduralRequestResolutionRequest,
    ProceduralRequestResolutionResponse,
    ProceduralRequestStatus,
    ProceduralRequestType,
    ProceduralRequestView,
    SessionActionRequest,
    SessionActionResponse,
    SessionCreate,
    SessionEventPayload,
    SessionEventView,
    SessionView,
    UserRole,
)
from mootcourt.services.case_visibility import get_case_package_model


class SessionServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def create_court_session(unit_of_work: SqlAlchemyUnitOfWork, request: SessionCreate) -> str:
    # 1.1 验证案件包是否存在
    package = await get_case_package_model(
        unit_of_work, request.case_id, package_version=request.package_version
    )
    if package is None:
        raise SessionServiceError("case_not_found", "case package not found", 404)
    # 1.2 创建会话模型
    model = await unit_of_work.court_sessions.add_session(
        package_id=package.id,
        user_role=request.user_role.value,
        phase=CourtPhase.COURT_OPENING.value,
        initial_event_payload={
            "case_id": package.case_id,
            "package_version": package.package_version,
        },
    )
    return model.id


async def get_session_view(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> SessionView | None:
    model = await unit_of_work.court_sessions.get(session_id)
    if model is None:
        return None

    package = await unit_of_work.case_packages.get_by_database_id(model.package_id)
    if package is None:
        return None

    phase = CourtPhase(model.phase)
    role = Role(model.user_role)
    legal_actions = list(allowed_actions(phase, role))

    return SessionView(
        session_id=model.id,
        case_id=package.case_id,
        package_version=package.package_version,
        user_role=UserRole(model.user_role),
        phase=phase,
        status=model.status,
        turns_used=model.turns_used,
        allowed_actions=legal_actions,
        submitted_evidence_ids=await unit_of_work.court_sessions.submitted_ids(session_id),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


async def list_session_events(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> list[SessionEventView] | None:
    if await unit_of_work.court_sessions.get(session_id) is None:
        return None
    rows = await unit_of_work.court_sessions.list_events(session_id)
    return [_event_view(item) for item in rows]


async def list_evidence_statuses(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> list[EvidenceStatusView] | None:
    session = await unit_of_work.court_sessions.get(session_id)
    if session is None:
        return None
    evidence = await unit_of_work.court_sessions.list_package_evidence(session.package_id)
    submissions = {
        item.evidence_id: item
        for item in await unit_of_work.court_sessions.list_evidence_submissions(session_id)
    }
    return [
        EvidenceStatusView(
            evidence_id=item.evidence_id,
            title=item.title,
            available_to_current_role=session.user_role in item.available_to,
            status=(
                EvidenceSubmissionStatus.SUBMITTED
                if item.evidence_id in submissions
                else EvidenceSubmissionStatus.NOT_SUBMITTED
            ),
            submitted_by=(
                UserRole(submissions[item.evidence_id].submitted_by)
                if item.evidence_id in submissions
                else None
            ),
            submitted_at=(
                submissions[item.evidence_id].created_at
                if item.evidence_id in submissions
                else None
            ),
        )
        for item in evidence
    ]


async def list_procedural_requests(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> list[ProceduralRequestView] | None:
    if await unit_of_work.court_sessions.get(session_id) is None:
        return None
    rows = await unit_of_work.court_sessions.list_procedural_requests(session_id)
    return [_procedural_request_view(item) for item in rows]


async def resolve_procedural_request(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request_id: str,
    request: ProceduralRequestResolutionRequest,
) -> ProceduralRequestResolutionResponse:
    session = await unit_of_work.court_sessions.get_for_update(session_id)
    if session is None:
        raise SessionServiceError("session_not_found", "court session not found", 404)
    model = await unit_of_work.court_sessions.get_procedural_request_for_update(
        session_id, request_id
    )
    if model is None:
        raise SessionServiceError(
            "procedural_request_not_found", "procedural request not found in this session", 404
        )
    if model.resolution is not None:
        raise SessionServiceError(
            "procedural_request_already_resolved",
            "procedural request has already been resolved",
        )

    # 问题制止请求由教学控制者批准或驳回；质证记录只确认记入评议，不模拟法院裁判。
    allowed = (
        {"APPROVED", "REJECTED"}
        if model.status == ProceduralRequestStatus.PENDING_CONTROLLER_REVIEW.value
        else {"RECORDED"}
    )
    if request.resolution.value not in allowed:
        raise SessionServiceError(
            "procedural_resolution_mismatch",
            f"resolution must be one of {sorted(allowed)} for the current request status",
            422,
        )

    sequence_number = await unit_of_work.court_sessions.next_event_sequence(session_id)
    resolved_at = datetime.now(UTC)
    await unit_of_work.court_sessions.resolve_procedural_request(
        model,
        resolution=request.resolution.value,
        reason=request.reason,
        event_sequence_number=sequence_number,
        resolved_at=resolved_at,
    )
    event = await unit_of_work.court_sessions.add_event(
        session_id=session_id,
        sequence_number=sequence_number,
        phase=session.phase,
        actor_role=Role.CONTROLLER.value,
        action="procedural_request_resolved",
        payload={
            "procedural_request_id": model.id,
            "procedural_request_type": model.request_type,
            "procedural_request_status": model.status,
            "resolution": model.resolution,
            "resolution_reason": model.resolution_reason,
            "resolution_event_sequence": sequence_number,
            "resulting_phase": session.phase,
        },
    )
    return ProceduralRequestResolutionResponse(
        request=_procedural_request_view(model), event=_event_view(event)
    )


async def get_evidence_fact_summary(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> list[EvidenceFactSummaryView] | None:
    session = await unit_of_work.court_sessions.get(session_id)
    if session is None:
        return None
    facts = await unit_of_work.court_sessions.list_package_facts(session.package_id)
    evidence = await unit_of_work.court_sessions.list_package_evidence(session.package_id)
    submitted = set(await unit_of_work.court_sessions.submitted_ids(session_id))
    traces = await unit_of_work.court_sessions.list_participant_statement_traces(session_id)

    result: list[EvidenceFactSummaryView] = []
    for fact in facts:
        related_ids = sorted(
            item.evidence_id
            for item in evidence
            if fact.fact_id in item.payload.get("related_fact_ids", [])
        )
        submitted_ids = [item for item in related_ids if item in submitted]
        appeared_statement_ids = sorted(
            {
                statement_id
                for trace in traces
                if fact.fact_id in trace.related_fact_ids
                for statement_id in trace.supported_statement_ids
            }
        )
        if not submitted_ids:
            support_status = EvidenceFactSupportStatus.NO_SUBMITTED_SUPPORT
        elif len(submitted_ids) == len(related_ids):
            support_status = EvidenceFactSupportStatus.SUPPORTED_BY_SUBMITTED_EVIDENCE
        else:
            support_status = EvidenceFactSupportStatus.PARTIALLY_SUPPORTED
        result.append(
            EvidenceFactSummaryView(
                fact_id=fact.fact_id,
                description=fact.description,
                fact_record_status=fact.status,
                related_evidence_ids=related_ids,
                submitted_evidence_ids=submitted_ids,
                unsubmitted_evidence_ids=[item for item in related_ids if item not in submitted],
                appeared_statement_ids=appeared_statement_ids,
                support_status=support_status,
            )
        )
    return result


async def apply_session_action(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: SessionActionRequest,
    settings: Settings,
) -> SessionActionResponse:
    model = await unit_of_work.court_sessions.get_for_update(session_id)
    if model is None:
        raise SessionServiceError("session_not_found", "court session not found", 404)
    if model.status != "active":
        raise SessionServiceError("session_closed", "court session is already completed")

    phase = CourtPhase(model.phase)
    actor_role = (
        Role.CONTROLLER if request.action is CourtAction.ADVANCE_PHASE else Role(model.user_role)
    )
    decision = validate_action(
        phase,
        ActionRequest(
            role=actor_role,
            action=request.action,
            target_id=request.target_id,
            evidence_ids=request.evidence_ids,
        ),
    )
    if not decision.allowed:
        raise SessionServiceError("action_not_allowed", decision.reason or "action not allowed")
    if actor_role is not Role.CONTROLLER and model.turns_used >= settings.session_max_turns:
        raise SessionServiceError("turn_limit_reached", "session turn limit reached")

    procedural_status = await validate_action_payload(
        unit_of_work, model.package_id, model.id, actor_role, request
    )
    event_phase = phase
    if request.action is CourtAction.ADVANCE_PHASE:
        model.phase = next_phase(phase).value
        if CourtPhase(model.phase) is CourtPhase.COMPLETED:
            model.status = "completed"
    elif request.action is not CourtAction.COMPLETE_PHASE:
        model.turns_used += 1

    if request.action is CourtAction.SUBMIT_EVIDENCE:
        unit_of_work.court_sessions.add_evidence_submissions(
            model.id, request.evidence_ids, actor_role.value
        )

    event_sequence_number = await unit_of_work.court_sessions.next_event_sequence(model.id)
    procedural_request = None
    if request.action in {
        CourtAction.RAISE_PROCEDURAL_REQUEST,
        CourtAction.CHALLENGE_EVIDENCE,
    }:
        request_type = (
            ProceduralRequestType.EVIDENCE_CHALLENGE
            if request.action is CourtAction.CHALLENGE_EVIDENCE
            else request.procedural_request_type
        )
        if request_type is None or procedural_status is None:
            raise RuntimeError("validated procedural request is missing structured fields")
        procedural_request = await unit_of_work.court_sessions.add_procedural_request(
            session_id=model.id,
            request_type=request_type.value,
            raised_by=actor_role.value,
            event_sequence_number=event_sequence_number,
            target_event_sequence=request.target_event_sequence,
            evidence_ids=request.evidence_ids,
            challenge_dimensions=[item.value for item in request.challenge_dimensions],
            content=request.content or "",
            status=procedural_status.value,
        )

    event = await unit_of_work.court_sessions.add_event(
        session_id=model.id,
        sequence_number=event_sequence_number,
        phase=event_phase.value,
        actor_role=actor_role.value,
        action=request.action.value,
        payload={
            "target_id": request.target_id,
            "evidence_ids": request.evidence_ids,
            "content": request.content,
            "resulting_phase": model.phase,
            "procedural_request_id": (
                procedural_request.id if procedural_request is not None else None
            ),
            "procedural_request_type": (
                procedural_request.request_type if procedural_request is not None else None
            ),
            "procedural_request_status": (
                procedural_request.status if procedural_request is not None else None
            ),
            "target_event_sequence": request.target_event_sequence,
            "challenge_dimensions": [item.value for item in request.challenge_dimensions],
        },
    )
    await unit_of_work.court_sessions.flush_session(model)
    view = await get_session_view(unit_of_work, model.id)
    if view is None:
        raise RuntimeError("session disappeared after action flush")
    return SessionActionResponse(
        session=view,
        event=_event_view(event),
        agent_invoked=False,
        fixed_response=_fixed_response(request.action, CourtPhase(model.phase)),
    )


async def validate_action_payload(
    unit_of_work: SqlAlchemyUnitOfWork,
    package_id: int,
    session_id: str,
    actor_role: Role,
    request: SessionActionRequest,
) -> ProceduralRequestStatus | None:
    if (
        request.action
        in {
            CourtAction.MAKE_STATEMENT,
            CourtAction.QUESTION_PARTICIPANT,
            CourtAction.RAISE_PROCEDURAL_REQUEST,
            CourtAction.CHALLENGE_EVIDENCE,
        }
        and not request.content
    ):
        raise SessionServiceError("content_required", "content is required for this action", 422)

    if (
        request.action in {CourtAction.SUBMIT_EVIDENCE, CourtAction.CHALLENGE_EVIDENCE}
        and not request.evidence_ids
    ):
        raise SessionServiceError("evidence_required", "at least one evidence ID is required", 422)
    if request.action in {CourtAction.SUBMIT_EVIDENCE, CourtAction.CHALLENGE_EVIDENCE}:
        rows = await unit_of_work.court_sessions.evidence_by_ids(package_id, request.evidence_ids)
        by_id = {item.evidence_id: item for item in rows}
        missing = set(request.evidence_ids) - set(by_id)
        if missing:
            raise SessionServiceError(
                "unknown_evidence", f"unknown evidence IDs: {sorted(missing)}", 422
            )
        unauthorized = [
            evidence_id
            for evidence_id, item in by_id.items()
            if actor_role.value not in item.available_to
        ]
        if unauthorized:
            raise SessionServiceError(
                "evidence_forbidden",
                f"evidence is not available to this role: {unauthorized}",
                403,
            )

    if request.action is CourtAction.SUBMIT_EVIDENCE:
        existing = await unit_of_work.court_sessions.submitted_ids_from(
            session_id, request.evidence_ids
        )
        if existing:
            raise SessionServiceError(
                "evidence_already_submitted", f"evidence already submitted: {sorted(existing)}"
            )

    if request.action is CourtAction.CHALLENGE_EVIDENCE:
        submitted = await unit_of_work.court_sessions.submitted_ids_from(
            session_id, request.evidence_ids
        )
        missing_submissions = set(request.evidence_ids) - submitted
        if missing_submissions:
            raise SessionServiceError(
                "evidence_not_submitted",
                f"evidence has not been submitted: {sorted(missing_submissions)}",
            )
        if not request.challenge_dimensions:
            raise SessionServiceError(
                "challenge_dimension_required",
                "at least one evidence challenge dimension is required",
                422,
            )
        if len(request.challenge_dimensions) != len(set(request.challenge_dimensions)):
            raise SessionServiceError(
                "duplicate_challenge_dimension",
                "evidence challenge dimensions must be unique",
                422,
            )
        if request.procedural_request_type not in {None, ProceduralRequestType.EVIDENCE_CHALLENGE}:
            raise SessionServiceError(
                "procedural_request_type_mismatch",
                "challenge_evidence only supports EVIDENCE_CHALLENGE",
                422,
            )

    if request.action is CourtAction.QUESTION_PARTICIPANT:
        if request.target_id is None:
            raise SessionServiceError("target_required", "participant target is required", 422)
        if not await unit_of_work.court_sessions.participant_exists(package_id, request.target_id):
            raise SessionServiceError("unknown_participant", "participant target not found", 422)

    if request.action is CourtAction.RAISE_PROCEDURAL_REQUEST:
        if request.evidence_ids or request.challenge_dimensions:
            raise SessionServiceError(
                "procedural_request_payload_mismatch",
                "question-control requests cannot include evidence challenge fields",
                422,
            )
        if request.procedural_request_type not in {
            ProceduralRequestType.IRRELEVANT_QUESTION,
            ProceduralRequestType.REPETITIVE_QUESTION,
            ProceduralRequestType.IMPROPER_QUESTION,
        }:
            raise SessionServiceError(
                "procedural_request_type_required",
                "a supported question-control request type is required",
                422,
            )
        if request.target_event_sequence is None:
            raise SessionServiceError(
                "target_event_required", "a prior question event is required", 422
            )
        target = await unit_of_work.court_sessions.get_event_by_sequence(
            session_id, request.target_event_sequence
        )
        if target is None or target.action != CourtAction.QUESTION_PARTICIPANT.value:
            raise SessionServiceError(
                "target_event_not_question",
                "procedural request must target a prior question event",
                422,
            )
        if request.procedural_request_type is ProceduralRequestType.REPETITIVE_QUESTION:
            content = _normalize_question(str(target.payload.get("content") or ""))
            earlier_questions = await unit_of_work.court_sessions.earlier_question_events(
                session_id, target.sequence_number
            )
            if not any(
                _normalize_question(str(item.payload.get("content") or "")) == content
                for item in earlier_questions
            ):
                raise SessionServiceError(
                    "question_not_repetitive",
                    "the target question does not repeat an earlier recorded question",
                    422,
                )
        return ProceduralRequestStatus.PENDING_CONTROLLER_REVIEW

    if request.action is CourtAction.CHALLENGE_EVIDENCE:
        return ProceduralRequestStatus.RECORDED_FOR_EVALUATION
    return None


def _normalize_question(content: str) -> str:
    # 重复判断只做确定性的空白和末尾标点归一化，不尝试语义等价推断。
    return "".join(content.split()).rstrip("？?!！。.")


def _procedural_request_view(item: ProceduralRequestRecord) -> ProceduralRequestView:
    return ProceduralRequestView.model_validate(
        {
            "id": item.id,
            "session_id": item.session_id,
            "request_type": item.request_type,
            "raised_by": item.raised_by,
            "event_sequence_number": item.event_sequence_number,
            "target_event_sequence": item.target_event_sequence,
            "evidence_ids": item.evidence_ids,
            "challenge_dimensions": item.challenge_dimensions,
            "content": item.content,
            "status": item.status,
            "resolution": item.resolution,
            "resolution_reason": item.resolution_reason,
            "resolved_at": item.resolved_at,
            "resolution_event_sequence": item.resolution_event_sequence,
            "created_at": item.created_at,
        }
    )


def _event_view(event: SessionEventRecord) -> SessionEventView:
    action: (
        CourtAction
        | Literal[
            "session_created",
            "procedural_request_resolved",
            "new_statement_reviewed",
            "court_review_generated",
        ]
    )
    if event.action == "session_created":
        action = "session_created"
    elif event.action == "procedural_request_resolved":
        action = "procedural_request_resolved"
    elif event.action == "new_statement_reviewed":
        action = "new_statement_reviewed"
    elif event.action == "court_review_generated":
        action = "court_review_generated"
    else:
        action = CourtAction(event.action)
    return SessionEventView(
        sequence_number=event.sequence_number,
        phase=CourtPhase(event.phase),
        actor_role=Role(event.actor_role),
        action=action,
        payload=SessionEventPayload.model_validate(event.payload),
        created_at=event.created_at,
    )


def _fixed_response(action: CourtAction, phase: CourtPhase) -> str:
    if action is CourtAction.ADVANCE_PHASE:
        return f"庭审已推进至 {phase.value}。"
    if action is CourtAction.SUBMIT_EVIDENCE:
        return "证据已完成权限校验并写入庭审记录。"
    if action is CourtAction.CHALLENGE_EVIDENCE:
        return "质证意见已写入庭审记录。"
    return "操作已通过阶段和权限校验，并写入庭审记录。"
