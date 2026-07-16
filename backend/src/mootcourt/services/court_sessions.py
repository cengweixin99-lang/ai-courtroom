from __future__ import annotations

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
from mootcourt.repositories.court_sessions import SessionEventRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.runtime import (
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

    if phase is not CourtPhase.COMPLETED:
        legal_actions.append(CourtAction.ADVANCE_PHASE)

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

    await validate_action_payload(unit_of_work, model.package_id, model.id, actor_role, request)
    event_phase = phase
    if request.action is CourtAction.ADVANCE_PHASE:
        model.phase = next_phase(phase).value
        if CourtPhase(model.phase) is CourtPhase.COMPLETED:
            model.status = "completed"
    else:
        model.turns_used += 1

    if request.action is CourtAction.SUBMIT_EVIDENCE:
        unit_of_work.court_sessions.add_evidence_submissions(
            model.id, request.evidence_ids, actor_role.value
        )

    event = await unit_of_work.court_sessions.add_event(
        session_id=model.id,
        sequence_number=await unit_of_work.court_sessions.next_event_sequence(model.id),
        phase=event_phase.value,
        actor_role=actor_role.value,
        action=request.action.value,
        payload={
            "target_id": request.target_id,
            "evidence_ids": request.evidence_ids,
            "content": request.content,
            "resulting_phase": model.phase,
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
) -> None:
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

    if request.action in {CourtAction.SUBMIT_EVIDENCE, CourtAction.CHALLENGE_EVIDENCE}:
        if not request.evidence_ids:
            raise SessionServiceError(
                "evidence_required", "at least one evidence ID is required", 422
            )
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

    if request.action is CourtAction.QUESTION_PARTICIPANT:
        if request.target_id is None:
            raise SessionServiceError("target_required", "participant target is required", 422)
        if not await unit_of_work.court_sessions.participant_exists(package_id, request.target_id):
            raise SessionServiceError("unknown_participant", "participant target not found", 422)


def _event_view(event: SessionEventRecord) -> SessionEventView:
    action: CourtAction | Literal["session_created"] = (
        "session_created" if event.action == "session_created" else CourtAction(event.action)
    )
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
