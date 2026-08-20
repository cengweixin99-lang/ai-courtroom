from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

from mootcourt.domain.courtroom import CourtPhase, Role
from mootcourt.repositories.case_packages import CasePackageRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.agents import (
    AgentCaseContext,
    AgentContext,
    AgentEventImportance,
    AgentEvidenceContext,
    AgentFactContext,
    AgentHistoryEvent,
    AgentLegalSourceContext,
    AgentParticipantContext,
    AgentPublicClaim,
    AgentPublicStatement,
    AgentRole,
    AgentRoleMaterialContext,
    AgentTaskContext,
    AgentTurnRequest,
    ClaimType,
    PhaseSummary,
)
from mootcourt.schemas.case_package import (
    CaseRecord,
    EvidenceRecord,
    FactRecord,
    LegalProfile,
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
        await _participant_context(
            unit_of_work,
            session_id,
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
    legal_sources = _visible_legal_sources(package, request.actor_role)

    # 优先加载与当前任务相关的事件，而非简单取最近 N 条
    relevant_events = await unit_of_work.court_sessions.list_relevant_events(
        session_id,
        evidence_ids=list(request.evidence_ids) or None,
        target_id=request.target_id or request.participant_id,
        phase=phase.value,
    )
    case = CaseRecord.model_validate(package.case_data)

    # 已完成阶段的结构化摘要，帮助 Agent 把握全局
    phase_summaries = _build_phase_summaries(relevant_events, phase)

    # 事件分层 + 摘要压缩，减少 Token 消耗
    recent_events = [
        AgentHistoryEvent(
            sequence_number=item.sequence_number,
            phase=CourtPhase(item.phase),
            actor_role=Role(item.actor_role),
            action=item.action,
            content=None,  # 用 summary 替代完整 content 节省 Token
            importance=_event_importance(item),
            summary=_summarize_event(item),
        )
        for item in relevant_events
    ]

    # 律师角色加载本次庭审已提出的公开主张，防止前后矛盾
    role_public_claims = await _load_role_public_claims(
        unit_of_work, session_id, request.actor_role
    )
    # 同时加载对方律师的公开主张，便于组织针对性回应
    opposing_role = _opposing_advocate_role(request.actor_role)
    opposing_public_claims = (
        await _load_role_public_claims(unit_of_work, session_id, opposing_role)
        if opposing_role is not None
        else []
    )

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
        task=AgentTaskContext(
            target_id=request.target_id,
            evidence_ids=list(request.evidence_ids),
            challenge_dimensions=list(request.challenge_dimensions),
        ),
        facts=facts,
        evidence=evidence,
        role_materials=role_materials,
        participant=participant,
        phase_summaries=phase_summaries,
        role_public_claims=role_public_claims,
        opposing_public_claims=opposing_public_claims,
        legal_sources=legal_sources,
        recent_events=recent_events,
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
        AgentFactContext(
            id=fact.id,
            description=fact.description,
            status=fact.status,
            supporting_evidence_ids=fact.supporting_evidence_ids,
            contradicting_evidence_ids=fact.contradicting_evidence_ids,
        )
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
            related_fact_ids=evidence.related_fact_ids,
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


def _visible_legal_sources(
    package: CasePackageRecord, actor_role: AgentRole
) -> list[AgentLegalSourceContext]:
    """向控辩双方下发 LegalProfile 白名单法源，作为法律依据的唯一可引用集合。"""
    if actor_role not in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
        return []
    profile = LegalProfile.model_validate(package.legal_profile)
    categories: dict[str, Literal["substantive", "procedure", "evidence_rule"]] = {}
    for source_id in profile.substantive_source_ids:
        categories[source_id] = "substantive"
    for source_id in profile.procedure_source_ids:
        categories[source_id] = "procedure"
    for source_id in profile.evidence_rule_source_ids:
        categories[source_id] = "evidence_rule"
    sources: list[AgentLegalSourceContext] = []
    for row in package.legal_sources:
        if row.source_id not in categories:
            continue
        # 逐字段读取，payload 缺非关键字段（如 official_source_url）时不影响下发。
        payload = row.payload
        sources.append(
            AgentLegalSourceContext(
                source_id=row.source_id,
                instrument_title=str(payload.get("instrument_title") or ""),
                article_number=str(payload.get("article_number") or ""),
                category=categories[row.source_id],
                text=str(payload.get("text_snapshot") or "")[:1_000],
            )
        )
    return sources


def _opposing_advocate_role(actor_role: AgentRole) -> AgentRole | None:
    if actor_role is AgentRole.PROSECUTION:
        return AgentRole.DEFENSE
    if actor_role is AgentRole.DEFENSE:
        return AgentRole.PROSECUTION
    return None


async def _load_role_public_claims(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    actor_role: AgentRole,
) -> list[AgentPublicClaim]:
    """加载律师角色在本次庭审中已经提出的公开主张。"""
    if actor_role not in {AgentRole.PROSECUTION, AgentRole.DEFENSE}:
        return []
    rows = await unit_of_work.court_sessions.list_role_claims(
        session_id, actor_role.value, limit=20
    )
    return [
        AgentPublicClaim(
            sequence_number=row.event_sequence_number,
            phase=CourtPhase(row.phase),
            text=row.text[:300],
            fact_ids=list(row.fact_ids),
            claim_type=ClaimType(row.claim_type),
        )
        for row in rows
    ]


async def _participant_context(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    source: ParticipantRecord,
    participant_type: str,
    actor_role: AgentRole,
) -> AgentParticipantContext:
    if participant_type not in {"defendant", "witness"}:
        raise AgentContextError("participant_type_invalid", "unsupported participant type")

    public_statements: list[AgentPublicStatement] = []
    if participant_type in {"defendant", "witness"}:
        events = await unit_of_work.court_sessions.list_public_statements_for_participant(
            session_id, source.id, limit=10
        )
        public_statements = [
            AgentPublicStatement(
                sequence_number=event.sequence_number,
                phase=CourtPhase(event.phase),
                content=_summarize_public_statement(str(event.payload.get("content") or "")),
            )
            for event in events
        ]

    # 只显式复制允许字段，未来案卷 Schema 新增私有字段时不会自动进入模型上下文。
    return AgentParticipantContext(
        id=source.id,
        participant_type=cast(Literal["defendant", "witness"], participant_type),
        name=source.name,
        public_profile=source.public_profile,
        statements=source.statements,
        uncertainties=source.uncertainties,
        public_statements=public_statements,
        defense_position=(
            source.defense_position
            if participant_type == "defendant"
            and actor_role in {AgentRole.DEFENDANT, AgentRole.DEFENSE}
            else None
        ),
    )


def _summarize_public_statement(content: str, max_length: int = 500) -> str:
    """截断并清理公开陈述内容，用于多轮一致性上下文。"""
    content = content.strip().replace("\n", " ")
    if len(content) <= max_length:
        return content
    return content[: max_length - 3].rsplit(" ", 1)[0] + "..."


def _event_importance(event: Any) -> AgentEventImportance:
    """根据事件 action 判断其对 Agent 决策的重要性。"""
    critical_actions = {
        "advance_phase",
        "submit_evidence",
        "challenge_evidence",
        "resolve_procedural_request",
        "exclude_new_statement",
    }
    filler_actions = {
        "session_created",
        "complete_phase",
    }
    if event.action in critical_actions:
        return AgentEventImportance.CRITICAL
    if event.action in filler_actions:
        return AgentEventImportance.FILLER
    return AgentEventImportance.NORMAL


def _summarize_event(event: Any) -> str:
    """把长 content 压缩成一句话摘要，减少 Prompt Token。"""
    payload = event.payload or {}
    content = payload.get("content") or ""
    if isinstance(content, str) and len(content) > 120:
        content = content[:117] + "..."

    actor = event.actor_role
    action = event.action
    phase = event.phase

    if action == "advance_phase":
        return f"[{event.sequence_number}] 控制器推进阶段至 {phase}"
    if action == "submit_evidence":
        evidence_ids = payload.get("evidence_ids", [])
        return f"[{event.sequence_number}] {actor} 提交证据 {evidence_ids}"
    if action == "challenge_evidence":
        evidence_ids = payload.get("evidence_ids", [])
        return f"[{event.sequence_number}] {actor} 质证证据 {evidence_ids}"
    if action == "make_statement":
        target = payload.get("participant_id") or payload.get("target_id")
        prefix = f"针对 {target} " if target else ""
        return f"[{event.sequence_number}] {actor} {prefix}发表陈述: {content or '(无摘要)'}"
    if action == "question_participant":
        target = payload.get("target_id") or payload.get("participant_id")
        return f"[{event.sequence_number}] {actor} 向 {target} 发问: {content or '(无摘要)'}"
    if action == "resolve_procedural_request":
        return f"[{event.sequence_number}] 控制器处理程序请求: {content or '(无摘要)'}"
    return f"[{event.sequence_number}] {actor} {action}: {content or '(无摘要)'}"


def _build_phase_summaries(events: Sequence[Any], current_phase: CourtPhase) -> list[PhaseSummary]:
    """为当前阶段之前的所有阶段生成结构化摘要。"""
    summaries: dict[str, PhaseSummary] = {}
    for event in events:
        phase_value = event.phase
        if phase_value == current_phase.value:
            continue
        summary = summaries.setdefault(
            phase_value,
            PhaseSummary(phase=CourtPhase(phase_value)),
        )
        payload = event.payload or {}
        action = event.action
        content = payload.get("content") or ""

        if action == "submit_evidence":
            for evidence_id in payload.get("evidence_ids", []):
                if evidence_id not in summary.submitted_evidence_ids:
                    summary.submitted_evidence_ids.append(evidence_id)
        elif action == "challenge_evidence":
            for evidence_id in payload.get("evidence_ids", []):
                if evidence_id not in summary.challenged_evidence_ids:
                    summary.challenged_evidence_ids.append(evidence_id)
        elif action == "resolve_procedural_request":
            ruling = content[:120] if len(content) > 120 else content
            if ruling and ruling not in summary.procedural_rulings:
                summary.procedural_rulings.append(ruling)
        elif action == "make_statement" and len(content) > 10:
            statement = content[:120] if len(content) > 120 else content
            if statement and statement not in summary.key_statements:
                summary.key_statements.append(statement)

    return list(summaries.values())
