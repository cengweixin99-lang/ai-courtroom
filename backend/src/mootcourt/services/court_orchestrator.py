from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mootcourt.agents.providers import AgentProvider
from mootcourt.core.config import Settings
from mootcourt.domain.courtroom import CourtAction, CourtPhase, Role
from mootcourt.repositories.case_packages import CasePackageRecord
from mootcourt.repositories.court_sessions import SessionEventRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import AgentRole, AgentTurnRequest
from mootcourt.schemas.case_package import RoleMaterial
from mootcourt.schemas.reviews import NewStatementResolution, NewStatementResolutionRequest
from mootcourt.schemas.runtime import (
    AutoStepResponse,
    AutoStepStatus,
    EvidenceAgendaStatus,
    ProceduralRequestResolutionRequest,
    ProceduralRequestType,
    ProceduralResolution,
    SessionActionRequest,
    SessionEventView,
    SessionView,
)
from mootcourt.services.agent_turns import AgentStreamCallback, execute_agent_turn
from mootcourt.services.court_reviews import resolve_new_statement
from mootcourt.services.court_sessions import (
    apply_session_action,
    get_session_view,
    resolve_procedural_request,
)


class CourtOrchestratorError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PlannedAgentTurn:
    actor_role: AgentRole
    action: CourtAction
    participant_id: str | None = None
    target_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    challenge_dimensions: tuple[str, ...] = ()
    instruction: str | None = None


async def run_automatic_step(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    provider: AgentProvider,
    settings: Settings,
    stream_callback: AgentStreamCallback | None = None,
) -> AutoStepResponse:
    session = await get_session_view(unit_of_work, session_id)
    if session is None:
        raise CourtOrchestratorError("session_not_found", "court session not found", 404)
    if session.phase is CourtPhase.COMPLETED:
        return AutoStepResponse(
            status=AutoStepStatus.COMPLETED,
            session=session,
            message="庭审程序已经完成。",
        )

    review_step = await _resolve_pending_review(unit_of_work, session_id, session)
    if review_step is not None:
        return review_step

    events = await unit_of_work.court_sessions.list_events(session_id)
    if session.phase is CourtPhase.LEGAL_ANALYSIS:
        review = await unit_of_work.court_sessions.get_court_review(session_id)
        if review is not None:
            action_result = await apply_session_action(
                unit_of_work,
                session_id,
                SessionActionRequest(action=CourtAction.ADVANCE_PHASE),
                settings,
            )
            return AutoStepResponse(
                status=AutoStepStatus.PROGRESSED,
                session=action_result.session,
                event=action_result.event,
                message="复盘已生成，系统进入教学复盘阶段。",
            )
        return AutoStepResponse(
            status=AutoStepStatus.WAITING_FOR_REVIEW,
            session=session,
            message="庭审事实阶段结束，系统正在生成结构化教学复盘。",
        )
    if _should_wait_before_auto(session.phase, Role(session.user_role), events):
        return AutoStepResponse(
            status=AutoStepStatus.WAITING_FOR_USER,
            session=session,
            message="现在轮到你执行本方操作。",
        )

    plan = await _plan_agent_turn(unit_of_work, session, events)
    if plan is not None:
        result = await execute_agent_turn(
            unit_of_work,
            session_id,
            AgentTurnRequest(
                actor_role=plan.actor_role,
                action=plan.action,
                participant_id=plan.participant_id,
                target_id=plan.target_id,
                evidence_ids=list(plan.evidence_ids),
                challenge_dimensions=list(plan.challenge_dimensions),
                instruction=plan.instruction,
            ),
            provider,
            settings,
            stream_callback=stream_callback,
        )
        if result.status.value == "failed":
            return AutoStepResponse(
                status=AutoStepStatus.FAILED,
                session=result.session,
                message=result.error.message if result.error else "自动角色调用失败。",
                error=result.error,
            )
        return AutoStepResponse(
            status=AutoStepStatus.PROGRESSED,
            session=result.session,
            event=result.event,
            message="系统已完成一个自动角色回合。",
        )

    if _waiting_for_user(session.phase, Role(session.user_role), events):
        return AutoStepResponse(
            status=AutoStepStatus.WAITING_FOR_USER,
            session=session,
            message="现在轮到你执行本方操作。",
        )

    action_result = await apply_session_action(
        unit_of_work,
        session_id,
        SessionActionRequest(action=CourtAction.ADVANCE_PHASE),
        settings,
    )
    return AutoStepResponse(
        status=(
            AutoStepStatus.COMPLETED
            if action_result.session.phase is CourtPhase.COMPLETED
            else AutoStepStatus.PROGRESSED
        ),
        session=action_result.session,
        event=action_result.event,
        message="系统已自动推进庭审阶段。",
    )


async def _resolve_pending_review(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    session: SessionView,
) -> AutoStepResponse | None:
    requests = await unit_of_work.court_sessions.list_procedural_requests(session_id)
    pending_request = next((item for item in requests if item.resolution is None), None)
    if pending_request is not None:
        result = await resolve_procedural_request(
            unit_of_work,
            session_id,
            pending_request.id,
            ProceduralRequestResolutionRequest(
                resolution=(
                    ProceduralResolution.RECORDED
                    if pending_request.request_type
                    == ProceduralRequestType.EVIDENCE_CHALLENGE.value
                    else ProceduralResolution.APPROVED
                ),
                reason=(
                    "质证意见已自动记入教学评议。"
                    if pending_request.request_type
                    == ProceduralRequestType.EVIDENCE_CHALLENGE.value
                    else "教学控制器完成硬性校验后自动处理该程序请求。"
                ),
            ),
        )
        return AutoStepResponse(
            status=AutoStepStatus.PROGRESSED,
            session=session,
            event=result.event,
            message="教学控制器已自动处理程序请求。",
        )
    statements = await unit_of_work.court_sessions.list_participant_statement_traces(session_id)
    pending_statement = next(
        (item for item in statements if item.new_statement and item.review_status is None),
        None,
    )
    if pending_statement is None:
        return None
    await resolve_new_statement(
        unit_of_work,
        session_id,
        pending_statement.id,
        NewStatementResolutionRequest(
            resolution=NewStatementResolution.EXCLUDED_FROM_RECORD,
            reason="案卷外新增陈述保守排除，不进入本庭事实认定材料。",
        ),
    )
    events = await unit_of_work.court_sessions.list_events(session_id)
    return AutoStepResponse(
        status=AutoStepStatus.PROGRESSED,
        session=session,
        event=SessionEventView.model_validate(events[-1]),
        message="教学控制器已自动排除案卷外新增陈述。",
    )


async def _plan_agent_turn(
    unit_of_work: SqlAlchemyUnitOfWork,
    session: SessionView,
    events: Sequence[SessionEventRecord],
) -> PlannedAgentTurn | None:
    phase = session.phase
    user_role = Role(session.user_role)
    package = await unit_of_work.case_packages.get_runtime_package(
        session.case_id, session.package_version
    )
    if package is None:
        raise CourtOrchestratorError("case_not_found", "session case package not found", 404)

    if phase is CourtPhase.INDICTMENT_AND_DEFENDANT_STATEMENT:
        if user_role is Role.DEFENSE and not _has_action(events, phase, Role.PROSECUTION):
            return PlannedAgentTurn(AgentRole.PROSECUTION, CourtAction.MAKE_STATEMENT)
        if not _has_action(events, phase, Role.DEFENDANT):
            return PlannedAgentTurn(
                AgentRole.DEFENDANT,
                CourtAction.MAKE_STATEMENT,
                participant_id=_defendant_id(package),
            )
    elif phase is CourtPhase.COURT_INVESTIGATION:
        other = Role.DEFENSE if user_role is Role.PROSECUTION else Role.PROSECUTION
        if not _has_action(events, phase, other):
            return PlannedAgentTurn(AgentRole(other.value), CourtAction.MAKE_STATEMENT)
        if not _has_action(events, phase, Role.DEFENDANT):
            return PlannedAgentTurn(
                AgentRole.DEFENDANT,
                CourtAction.MAKE_STATEMENT,
                participant_id=_defendant_id(package),
            )
    elif phase in {
        CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION,
        CourtPhase.DEFENSE_EVIDENCE_AND_EXAMINATION,
    }:
        presenting = (
            Role.PROSECUTION
            if phase is CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION
            else Role.DEFENSE
        )
        if user_role is not presenting and not _has_action(
            events, phase, presenting, CourtAction.SUBMIT_EVIDENCE
        ):
            evidence_ids = await _automatic_evidence_ids(unit_of_work, session, package, presenting)
            if evidence_ids:
                return PlannedAgentTurn(
                    AgentRole(presenting.value),
                    CourtAction.SUBMIT_EVIDENCE,
                    evidence_ids=tuple(evidence_ids),
                )
        opposing = Role.DEFENSE if presenting is Role.PROSECUTION else Role.PROSECUTION
        pending_agenda = await unit_of_work.court_sessions.list_evidence_agenda(
            session.session_id,
            phase=phase.value,
            responding_role=opposing.value,
            status=EvidenceAgendaStatus.PENDING.value,
        )
        if user_role is not opposing and pending_agenda:
            return PlannedAgentTurn(
                AgentRole(opposing.value),
                CourtAction.CHALLENGE_EVIDENCE,
                # 每轮最多处理三项，剩余证据由议程状态驱动后续自动回合。
                evidence_ids=tuple(item.evidence_id for item in pending_agenda[:3]),
                challenge_dimensions=("AUTHENTICITY", "RELEVANCE", "PROBATIVE_VALUE"),
                instruction=(
                    "请从本阶段已提交证据中选择本方需要质证的项目，发表结构化质证意见；"
                    "无需为了覆盖全部证据而提出无实质意义的异议。"
                ),
            )
    elif phase is CourtPhase.WITNESS_QUESTIONING:
        unanswered = _unanswered_question(events)
        if unanswered is not None:
            return PlannedAgentTurn(
                AgentRole.WITNESS,
                CourtAction.MAKE_STATEMENT,
                participant_id=str(unanswered.payload["target_id"]),
                instruction=str(unanswered.payload.get("content") or "请回答刚才的问题。"),
            )
        other = Role.DEFENSE if user_role is Role.PROSECUTION else Role.PROSECUTION
        for participant in package.participants:
            if participant.participant_type != "witness":
                continue
            if not _has_targeted_action(
                events, phase, other, CourtAction.QUESTION_PARTICIPANT, participant.participant_id
            ):
                return PlannedAgentTurn(
                    AgentRole(other.value),
                    CourtAction.QUESTION_PARTICIPANT,
                    target_id=participant.participant_id,
                    instruction="请围绕本方有权访问的案卷材料询问该证人。",
                )
    elif phase in {
        CourtPhase.COURT_DEBATE_PROSECUTION,
        CourtPhase.COURT_DEBATE_DEFENSE,
    }:
        speaker = Role.PROSECUTION if phase is CourtPhase.COURT_DEBATE_PROSECUTION else Role.DEFENSE
        if user_role is not speaker and not _has_action(events, phase, speaker):
            return PlannedAgentTurn(AgentRole(speaker.value), CourtAction.MAKE_STATEMENT)
    elif phase is CourtPhase.DEFENDANT_FINAL_STATEMENT and not _has_action(
        events, phase, Role.DEFENDANT
    ):
        return PlannedAgentTurn(
            AgentRole.DEFENDANT,
            CourtAction.MAKE_STATEMENT,
            participant_id=_defendant_id(package),
        )
    return None


def _waiting_for_user(
    phase: CourtPhase, user_role: Role, events: Sequence[SessionEventRecord]
) -> bool:
    user_phases = {
        CourtPhase.INDICTMENT_AND_DEFENDANT_STATEMENT: {Role.PROSECUTION},
        CourtPhase.COURT_INVESTIGATION: {Role.PROSECUTION, Role.DEFENSE},
        CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION: {Role.PROSECUTION, Role.DEFENSE},
        CourtPhase.DEFENSE_EVIDENCE_AND_EXAMINATION: {Role.PROSECUTION, Role.DEFENSE},
        CourtPhase.WITNESS_QUESTIONING: {Role.PROSECUTION, Role.DEFENSE},
        CourtPhase.COURT_DEBATE_PROSECUTION: {Role.PROSECUTION},
        CourtPhase.COURT_DEBATE_DEFENSE: {Role.DEFENSE},
        CourtPhase.REVIEW: {Role.PROSECUTION, Role.DEFENSE},
    }
    if user_role not in user_phases.get(phase, set()):
        return False
    return not _has_action(events, phase, user_role, CourtAction.COMPLETE_PHASE)


def _should_wait_before_auto(
    phase: CourtPhase, user_role: Role, events: Sequence[SessionEventRecord]
) -> bool:
    if not _waiting_for_user(phase, user_role, events):
        return False
    if phase in {
        CourtPhase.INDICTMENT_AND_DEFENDANT_STATEMENT,
        CourtPhase.COURT_INVESTIGATION,
        CourtPhase.COURT_DEBATE_PROSECUTION,
        CourtPhase.COURT_DEBATE_DEFENSE,
        CourtPhase.REVIEW,
    }:
        return True
    if phase is CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION:
        return user_role is Role.PROSECUTION or _has_action(
            events, phase, Role.PROSECUTION, CourtAction.SUBMIT_EVIDENCE
        )
    if phase is CourtPhase.DEFENSE_EVIDENCE_AND_EXAMINATION:
        return user_role is Role.DEFENSE or _has_action(
            events, phase, Role.DEFENSE, CourtAction.SUBMIT_EVIDENCE
        )
    # 证人先依案卷顺序作证，再由用户决定是否发问。
    return False


def _has_action(
    events: Sequence[SessionEventRecord],
    phase: CourtPhase,
    role: Role,
    action: CourtAction | None = None,
) -> bool:
    return any(
        item.phase == phase.value
        and item.actor_role == role.value
        and (action is None or item.action == action.value)
        for item in events
    )


def _has_targeted_action(
    events: Sequence[SessionEventRecord],
    phase: CourtPhase,
    role: Role,
    action: CourtAction,
    participant_id: str,
) -> bool:
    return any(
        item.phase == phase.value
        and item.actor_role == role.value
        and item.action == action.value
        and item.payload.get("target_id") == participant_id
        for item in events
    )


def _unanswered_question(
    events: Sequence[SessionEventRecord],
) -> SessionEventRecord | None:
    for question in reversed(events):
        if question.action != CourtAction.QUESTION_PARTICIPANT.value:
            continue
        target_id = question.payload.get("target_id")
        answered = any(
            item.sequence_number > question.sequence_number
            and item.action == CourtAction.MAKE_STATEMENT.value
            and item.payload.get("participant_id") == target_id
            for item in events
        )
        if not answered:
            return question
    return None


def _phase_evidence_ids(
    events: Sequence[SessionEventRecord], phase: CourtPhase, role: Role
) -> list[str]:
    return list(
        dict.fromkeys(
            evidence_id
            for item in events
            if item.phase == phase.value
            and item.actor_role == role.value
            and item.action == CourtAction.SUBMIT_EVIDENCE.value
            for evidence_id in item.payload.get("evidence_ids", [])
        )
    )


def _defendant_id(package: CasePackageRecord) -> str:
    participant = next(
        item for item in package.participants if item.participant_type == "defendant"
    )
    return str(participant.participant_id)


async def _automatic_evidence_ids(
    unit_of_work: SqlAlchemyUnitOfWork,
    session: SessionView,
    package: CasePackageRecord,
    role: Role,
) -> list[str]:
    submitted = set(await unit_of_work.court_sessions.submitted_ids(session.session_id))
    visible = {
        item.evidence_id
        for item in package.evidence
        if role.value in item.available_to and item.evidence_id not in submitted
    }
    priority = [
        evidence_id
        for row in package.role_materials
        if row.role == role.value
        for evidence_id in RoleMaterial.model_validate(row.payload).priority_evidence_ids
        if evidence_id in visible
    ]
    return priority[:3] or sorted(visible)[:1]
