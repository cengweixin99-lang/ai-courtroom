from __future__ import annotations

from typing import Literal, cast

from mootcourt.domain.courtroom import CourtPhase, Role
from mootcourt.repositories.case_packages import CasePackageRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import (
    AgentCaseContext,
    AgentContext,
    AgentEvidenceContext,
    AgentFactContext,
    AgentHistoryEvent,
    AgentParticipantContext,
    AgentRole,
    AgentRoleMaterialContext,
    AgentTurnRequest,
)
from mootcourt.schemas.case_package import (
    CaseRecord,
    EvidenceRecord,
    FactRecord,
    ParticipantRecord,
    RoleMaterial,
)


class AgentContextError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def build_agent_context(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    package_id: int,
    phase: CourtPhase,
    request: AgentTurnRequest,
) -> AgentContext:
    package = await unit_of_work.case_packages.get_runtime_package_by_database_id(package_id)
    if package is None:
        raise AgentContextError("case_not_found", "session case package not found")

    participant_id = request.participant_id or request.target_id
    participant_row = next(
        (item for item in package.participants if item.participant_id == participant_id),
        None,
    )
    participant_source = (
        ParticipantRecord.model_validate(participant_row.payload)
        if participant_row is not None
        else None
    )
    participant = (
        _participant_context(
            participant_source,
            participant_row.participant_type,
            request.actor_role,
        )
        if participant_row is not None and participant_source is not None
        else None
    )

    if request.actor_role in {AgentRole.WITNESS, AgentRole.DEFENDANT} and participant is None:
        raise AgentContextError("participant_not_found", "agent participant not found")

    facts = _visible_facts(package, request.actor_role, participant_source)
    evidence = _visible_evidence(package, request.actor_role)
    role_materials = _visible_role_materials(package, request.actor_role)
    recent_events = await unit_of_work.court_sessions.list_recent_events(session_id)
    case = CaseRecord.model_validate(package.case_data)

    return AgentContext(
        case=AgentCaseContext(
            case_id=package.case_id,
            package_version=package.package_version,
            title=case.title,
            summary=case.summary,
            jurisdiction=case.jurisdiction,
        ),
        actor_role=request.actor_role,
        phase=phase,
        action=request.action,
        facts=facts,
        evidence=evidence,
        role_materials=role_materials,
        participant=participant,
        recent_events=[
            AgentHistoryEvent(
                sequence_number=item.sequence_number,
                phase=CourtPhase(item.phase),
                actor_role=Role(item.actor_role),
                action=item.action,
                content=item.payload.get("content"),
            )
            for item in recent_events
        ],
    )


def _visible_facts(
    package: CasePackageRecord,
    actor_role: AgentRole,
    participant: ParticipantRecord | None,
) -> list[AgentFactContext]:
    if actor_role in {AgentRole.WITNESS, AgentRole.DEFENDANT}:
        # 证人只能看到 allowed_fact_ids，被告人只能看到 known_fact_ids；禁止事实 ID 本身也不下发。
        source_ids = (
            (
                participant.allowed_fact_ids
                if actor_role is AgentRole.WITNESS
                else participant.known_fact_ids
            )
            if participant
            else []
        )
        visible_ids = set(source_ids or [])
    else:
        visible_evidence_ids = {
            item.evidence_id for item in package.evidence if actor_role.value in item.available_to
        }
        visible_ids = {
            fact.id
            for row in package.facts
            for fact in [FactRecord.model_validate(row.payload)]
            if visible_evidence_ids.intersection(
                fact.supporting_evidence_ids + fact.contradicting_evidence_ids
            )
        }

    return [
        AgentFactContext(id=fact.id, description=fact.description, status=fact.status)
        for row in package.facts
        for fact in [FactRecord.model_validate(row.payload)]
        if fact.id in visible_ids
    ]


def _visible_evidence(
    package: CasePackageRecord, actor_role: AgentRole
) -> list[AgentEvidenceContext]:
    if actor_role not in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
        return []
    return [
        AgentEvidenceContext(
            id=evidence.id,
            title=evidence.title,
            content=evidence.content,
            reliability_notes=evidence.reliability_notes,
        )
        for row in package.evidence
        for evidence in [EvidenceRecord.model_validate(row.payload)]
        if actor_role.value in row.available_to
    ]


def _visible_role_materials(
    package: CasePackageRecord, actor_role: AgentRole
) -> list[AgentRoleMaterialContext]:
    if actor_role not in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
        return []
    return [
        AgentRoleMaterialContext(
            id=material.id,
            title=material.title,
            objectives=material.objectives,
            priority_evidence_ids=material.priority_evidence_ids,
            known_weaknesses=material.known_weaknesses,
        )
        for row in package.role_materials
        for material in [RoleMaterial.model_validate(row.payload)]
        if row.role == actor_role.value
    ]


def _participant_context(
    source: ParticipantRecord,
    participant_type: str,
    actor_role: AgentRole,
) -> AgentParticipantContext:
    if participant_type not in {"defendant", "witness"}:
        raise AgentContextError("participant_type_invalid", "unsupported participant type")
    # 只显式复制允许字段，未来案卷 Schema 新增私有字段时不会自动进入模型上下文。
    return AgentParticipantContext(
        id=source.id,
        participant_type=cast(Literal["defendant", "witness"], participant_type),
        name=source.name,
        public_profile=source.public_profile,
        statements=source.statements,
        uncertainties=source.uncertainties,
        defense_position=(
            source.defense_position
            if participant_type == "defendant"
            and actor_role in {AgentRole.DEFENDANT, AgentRole.DEFENSE}
            else None
        ),
    )
