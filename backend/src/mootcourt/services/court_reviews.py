from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mootcourt.agents.providers import StructuredAgentProvider, StructuredProviderRequest
from mootcourt.domain.courtroom import CourtPhase, Role
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import EvidenceRecord, FactRecord, LegalProfile, RoleMaterial
from mootcourt.schemas.legal_search import LegalSearchHit, LegalSearchOutcome
from mootcourt.schemas.reviews import (
    CourtReviewGenerateRequest,
    CourtReviewReport,
    ElementFindingStatus,
    FactFindingStatus,
    NewStatementResolutionRequest,
    NewStatementResolutionResponse,
    ReviewElementFinding,
    ReviewFactFinding,
    ReviewLegalCitation,
    ReviewLegalIssuesConfig,
    ReviewRecommendation,
    ReviewScoreDimension,
    ReviewTurnCheck,
    ReviewTurnDiagnostic,
    TurnQualityEvaluation,
    TurnQualityEvaluationBatch,
    TurnQualityEvaluationGenerateRequest,
    TurnQualityEvaluationReport,
)

_DISCLAIMER = "本报告仅用于虚构案件的教学模拟，不构成现实判决、法律意见或对任何真实人员的评价。"


class CourtReviewServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def resolve_new_statement(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    trace_id: str,
    request: NewStatementResolutionRequest,
) -> NewStatementResolutionResponse:
    session = await unit_of_work.court_sessions.get_for_update(session_id)
    if session is None:
        raise CourtReviewServiceError("session_not_found", "court session not found", 404)
    trace = await unit_of_work.court_sessions.get_participant_statement_trace_for_update(
        session_id, trace_id
    )
    if trace is None:
        raise CourtReviewServiceError(
            "statement_trace_not_found", "participant statement trace not found", 404
        )
    if not trace.new_statement:
        raise CourtReviewServiceError(
            "statement_not_new", "only a new in-court statement requires controller review", 422
        )
    if trace.review_status is not None:
        raise CourtReviewServiceError(
            "new_statement_already_reviewed", "new statement has already been reviewed"
        )

    sequence_number = await unit_of_work.court_sessions.next_event_sequence(session_id)
    reviewed_at = datetime.now(UTC)
    await unit_of_work.court_sessions.resolve_participant_statement_trace(
        trace,
        resolution=request.resolution.value,
        reason=request.reason,
        event_sequence_number=sequence_number,
        reviewed_at=reviewed_at,
    )
    # 纳入只表示保留为本庭陈述，不自动补写事实 ID 或将陈述视为已证实。
    await unit_of_work.court_sessions.add_event(
        session_id=session_id,
        sequence_number=sequence_number,
        phase=session.phase,
        actor_role=Role.CONTROLLER.value,
        action="new_statement_reviewed",
        payload={
            "statement_trace_id": trace.id,
            "statement_review_status": request.resolution.value,
            "content": request.reason,
            "resulting_phase": session.phase,
        },
    )
    return NewStatementResolutionResponse(
        trace_id=trace.id,
        resolution=request.resolution,
        reason=request.reason,
        review_event_sequence=sequence_number,
    )


async def generate_court_review(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: CourtReviewGenerateRequest,
) -> CourtReviewReport:
    session = await unit_of_work.court_sessions.get_for_update(session_id)
    if session is None:
        raise CourtReviewServiceError("session_not_found", "court session not found", 404)
    if CourtPhase(session.phase) not in {CourtPhase.LEGAL_ANALYSIS, CourtPhase.REVIEW}:
        raise CourtReviewServiceError(
            "review_phase_required", "court review can only be generated during legal analysis"
        )
    if await unit_of_work.court_sessions.get_court_review(session_id) is not None:
        raise CourtReviewServiceError("court_review_already_exists", "court review already exists")
    if len(request.legal_search_trace_ids) != len(set(request.legal_search_trace_ids)):
        raise CourtReviewServiceError(
            "duplicate_legal_trace", "legal search trace IDs must be unique", 422
        )

    package = await unit_of_work.case_packages.get_runtime_package_by_database_id(
        session.package_id
    )
    if package is None:
        raise CourtReviewServiceError("case_not_found", "session case package not found", 404)
    legal_config = ReviewLegalIssuesConfig.model_validate(package.legal_issues)
    legal_profile = LegalProfile.model_validate(package.legal_profile)
    statement_traces = await unit_of_work.court_sessions.list_participant_statement_traces(
        session_id
    )
    if any(item.new_statement and item.review_status is None for item in statement_traces):
        raise CourtReviewServiceError(
            "new_statement_review_pending", "all new statements must be reviewed first"
        )
    procedural_requests = await unit_of_work.court_sessions.list_procedural_requests(session_id)
    if any(item.resolution is None for item in procedural_requests):
        raise CourtReviewServiceError(
            "procedural_request_review_pending", "all procedural requests must be resolved first"
        )

    traces = await unit_of_work.legal_search_traces.get_many_for_package(
        session.package_id, request.legal_search_trace_ids
    )
    if len(traces) != len(request.legal_search_trace_ids):
        raise CourtReviewServiceError(
            "legal_trace_not_found",
            "one or more legal traces do not belong to this case version",
            422,
        )
    citations = _validated_citations(package.legal_sources, traces)
    required_source_ids = {
        source_id for element in legal_config.elements for source_id in element.legal_source_ids
    }
    missing_sources = required_source_ids - set(citations)
    if missing_sources:
        raise CourtReviewServiceError(
            "insufficient_legal_authority",
            f"required legal sources were not retrieved: {sorted(missing_sources)}",
            422,
        )

    submitted = set(await unit_of_work.court_sessions.submitted_ids(session_id))
    evidence_submissions = await unit_of_work.court_sessions.list_evidence_submissions(session_id)
    evidence_agenda = await unit_of_work.court_sessions.list_evidence_agenda(session_id)
    events = await unit_of_work.court_sessions.list_events(session_id)
    facts = [FactRecord.model_validate(item.payload) for item in package.facts]
    evidence = [EvidenceRecord.model_validate(item.payload) for item in package.evidence]
    fact_findings = _fact_findings(facts, submitted, statement_traces, procedural_requests)
    findings_by_id = {item.fact_id: item for item in fact_findings}
    element_findings = [
        _element_finding(element, findings_by_id, citations) for element in legal_config.elements
    ]
    score_dimensions, recommendations = _score_courtroom_learning(
        user_role=session.user_role,
        role_materials=[
            RoleMaterial.model_validate(item.payload) for item in package.role_materials
        ],
        evidence_submissions=evidence_submissions,
        evidence_agenda=evidence_agenda,
        required_source_ids=required_source_ids,
        citations=citations,
        fact_findings=fact_findings,
        element_findings=element_findings,
    )
    total_score = _weighted_total(score_dimensions)
    turn_diagnostics = _turn_diagnostics(
        user_role=session.user_role,
        events=events,
        evidence_fact_ids={item.id: set(item.related_fact_ids) for item in evidence},
    )
    unresolved_issue_ids = [
        issue.id
        for issue in legal_config.legal_issues
        if any(
            item.status
            in {
                ElementFindingStatus.DISPUTED,
                ElementFindingStatus.INSUFFICIENT,
                ElementFindingStatus.NOT_SATISFIED,
            }
            for item in element_findings
            if item.element_id in issue.element_ids
        )
    ]

    review_id = str(uuid4())
    sequence_number = await unit_of_work.court_sessions.next_event_sequence(session_id)
    created_at = datetime.now(UTC)
    report = CourtReviewReport(
        id=review_id,
        session_id=session_id,
        case_id=package.case_id,
        package_version=package.package_version,
        jurisdiction=legal_profile.jurisdiction,
        law_as_of_date=legal_profile.law_as_of_date,
        burden_of_proof=str(package.legal_profile["burden_of_proof"]["value"]),
        standard_of_proof=str(package.legal_profile["standard_of_proof"]["value"]),
        user_role=session.user_role,
        fact_findings=fact_findings,
        element_findings=element_findings,
        total_score=total_score,
        score_dimensions=score_dimensions,
        recommendations=recommendations,
        turn_diagnostics=turn_diagnostics,
        unresolved_issue_ids=unresolved_issue_ids,
        deterministic_conclusion_allowed=(
            legal_config.deterministic_conclusion_allowed and legal_profile.legal_conclusion_allowed
        ),
        conclusion=None,
        disclaimer=_DISCLAIMER,
        legal_search_trace_ids=request.legal_search_trace_ids,
        event_sequence_number=sequence_number,
        created_at=created_at,
    )
    await unit_of_work.court_sessions.add_court_review(
        review_id=review_id,
        session_id=session_id,
        event_sequence_number=sequence_number,
        legal_search_trace_ids=request.legal_search_trace_ids,
        report=report.model_dump(mode="json"),
        created_at=created_at,
    )
    await unit_of_work.court_sessions.add_event(
        session_id=session_id,
        sequence_number=sequence_number,
        phase=session.phase,
        actor_role=Role.CONTROLLER.value,
        action="court_review_generated",
        payload={
            "court_review_id": review_id,
            "content": "结构化教学复盘已生成。",
            "resulting_phase": session.phase,
        },
    )
    return report


async def get_court_review(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> CourtReviewReport | None:
    if await unit_of_work.court_sessions.get(session_id) is None:
        return None
    model = await unit_of_work.court_sessions.get_court_review(session_id)
    return CourtReviewReport.model_validate(model.report) if model is not None else None


async def generate_turn_quality_evaluation(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    request: TurnQualityEvaluationGenerateRequest,
    provider: StructuredAgentProvider,
) -> TurnQualityEvaluationReport:
    """生成独立教学点评，绝不回写庭审事件、确定性评分或法律结论。"""

    review_model = await unit_of_work.court_sessions.get_court_review(session_id)
    if review_model is None:
        raise CourtReviewServiceError("court_review_not_found", "court review not found", 404)
    if await unit_of_work.court_sessions.get_court_review_evaluation(review_model.id) is not None:
        raise CourtReviewServiceError(
            "turn_quality_evaluation_already_exists", "turn quality evaluation already exists"
        )
    review = CourtReviewReport.model_validate(review_model.report)
    diagnostics = {item.event_sequence_number: item for item in review.turn_diagnostics}
    selected_ids = request.event_sequence_numbers or sorted(diagnostics)
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise CourtReviewServiceError(
            "turn_diagnostic_selection_invalid", "invalid turn diagnostic selection", 422
        )
    selected = [diagnostics[item] for item in selected_ids if item in diagnostics]
    if len(selected) != len(selected_ids):
        raise CourtReviewServiceError(
            "turn_diagnostic_not_found", "selected turn is not available for evaluation", 422
        )
    events = {
        item.sequence_number: item
        for item in await unit_of_work.court_sessions.list_events(session_id)
    }
    payload = {
        "classification": "UNTRUSTED_COURTROOM_TRANSCRIPT",
        "rules": {
            "no_new_facts": True,
            "no_legal_conclusion": True,
            "rewrite_requires_evidence_and_fact_anchors": True,
            "allowed_event_sequences": selected_ids,
        },
        "turns": [
            {
                "diagnostic": item.model_dump(mode="json"),
                "content": str(events[item.event_sequence_number].payload.get("content") or ""),
            }
            for item in selected
            if item.event_sequence_number in events
        ],
    }
    schema = TurnQualityEvaluationBatch.model_json_schema()
    system = (
        "你是刑事模拟庭审教学点评器。只评价表达组织、对对方争点的回应和攻防策略；"
        "不得补充案卷事实、不得给出真实案件法律结论。若某项diagnostic的evidence_ids或"
        "fact_ids任一为空，rewritten_example必须为null。只输出严格符合 JSON Schema 的对象。"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    fallback = {"evaluations": [_fallback_turn_evaluation(item).model_dump() for item in selected]}
    result = await provider.generate_structured(
        StructuredProviderRequest(
            messages=(
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            schema_name="mootcourt_turn_quality_evaluation",
            response_schema=schema,
            fallback_output=fallback,
        )
    )
    try:
        batch = TurnQualityEvaluationBatch.model_validate(result.output)
        evaluations = _validate_turn_evaluations(batch.evaluations, diagnostics, selected_ids)
    except Exception as exc:
        raise CourtReviewServiceError(
            "turn_quality_evaluation_invalid", "model evaluation failed local validation", 502
        ) from exc
    report = TurnQualityEvaluationReport(
        id=str(uuid4()),
        review_id=review_model.id,
        session_id=session_id,
        provider=result.provider,
        model=result.model,
        evaluations=evaluations,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_cny=result.estimated_cost_cny,
        repair_count=0,
        created_at=datetime.now(UTC),
    )
    await unit_of_work.court_sessions.add_court_review_evaluation(
        evaluation_id=report.id,
        review_id=report.review_id,
        session_id=session_id,
        provider=report.provider,
        model=report.model,
        report=report.model_dump(mode="json"),
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
        estimated_cost_cny=report.estimated_cost_cny,
        repair_count=report.repair_count,
        created_at=report.created_at,
    )
    return report


async def get_turn_quality_evaluation(
    unit_of_work: SqlAlchemyUnitOfWork, session_id: str
) -> TurnQualityEvaluationReport | None:
    review = await unit_of_work.court_sessions.get_court_review(session_id)
    if review is None:
        return None
    model = await unit_of_work.court_sessions.get_court_review_evaluation(review.id)
    return TurnQualityEvaluationReport.model_validate(model.report) if model is not None else None


def _fallback_turn_evaluation(item: ReviewTurnDiagnostic) -> TurnQualityEvaluation:
    return TurnQualityEvaluation(
        event_sequence_number=item.event_sequence_number,
        organization_score=item.score,
        responsiveness_score=item.score,
        advocacy_score=item.score,
        strengths=[check.label for check in item.checks if check.passed][:3],
        improvements=[check.label for check in item.checks if not check.passed][:3],
        rewritten_example=(
            "保持当前证据锚点，依次说明事实、证据与本方结论的关系。"
            if item.evidence_ids and item.fact_ids
            else None
        ),
        evidence_ids=item.evidence_ids,
        fact_ids=item.fact_ids,
    )


def _validate_turn_evaluations(
    evaluations: list[TurnQualityEvaluation],
    diagnostics: dict[int, ReviewTurnDiagnostic],
    selected_ids: list[int],
) -> list[TurnQualityEvaluation]:
    by_sequence = {item.event_sequence_number: item for item in evaluations}
    if set(by_sequence) != set(selected_ids) or len(by_sequence) != len(evaluations):
        raise ValueError("model must evaluate every selected turn exactly once")
    for sequence, evaluation in by_sequence.items():
        diagnostic = diagnostics[sequence]
        if not set(evaluation.evidence_ids).issubset(diagnostic.evidence_ids):
            raise ValueError("model evaluation cited an unauthorized evidence ID")
        if not set(evaluation.fact_ids).issubset(diagnostic.fact_ids):
            raise ValueError("model evaluation cited an unauthorized fact ID")
        # 无完整锚点时即使模型违背提示返回改写，也在持久化前确定性清空。
        if not diagnostic.evidence_ids or not diagnostic.fact_ids:
            by_sequence[sequence] = evaluation.model_copy(update={"rewritten_example": None})
    return [by_sequence[item] for item in selected_ids]


def _validated_citations(
    legal_source_rows: list[Any], traces: list[Any]
) -> dict[str, ReviewLegalCitation]:
    sources = {row.source_id: row.payload for row in legal_source_rows}
    result: dict[str, ReviewLegalCitation] = {}
    for trace in traces:
        if trace.outcome != LegalSearchOutcome.SUFFICIENT_LEGAL_AUTHORITY.value:
            continue
        for raw_hit in trace.hits:
            hit = LegalSearchHit.model_validate(raw_hit)
            source = sources.get(hit.source_id)
            if source is None or not _citation_matches_source(hit, source):
                continue
            result.setdefault(
                hit.source_id,
                ReviewLegalCitation(
                    source_id=hit.source_id,
                    instrument_title=hit.instrument_title,
                    article_number=hit.article_number,
                    text=hit.text,
                    official_source_url=hit.official_source_url,
                    version_hash=hit.version_hash,
                    trace_id=trace.id,
                ),
            )
    return result


def _citation_matches_source(hit: LegalSearchHit, source: dict[str, Any]) -> bool:
    return (
        hit.article_number == source.get("article_number")
        and hit.text == source.get("text_snapshot")
        and hit.official_source_url == source.get("official_source_url")
        and hit.version_hash == source.get("version_hash")
    )


def _fact_findings(
    facts: list[FactRecord],
    submitted: set[str],
    statement_traces: list[Any],
    procedural_requests: list[Any],
) -> list[ReviewFactFinding]:
    appeared_statement_ids = {
        statement_id
        for trace in statement_traces
        if not trace.new_statement or trace.review_status == "INCLUDED_IN_RECORD"
        for statement_id in trace.supported_statement_ids
    }
    challenged_ids = {
        evidence_id
        for request in procedural_requests
        if request.request_type == "EVIDENCE_CHALLENGE"
        for evidence_id in request.evidence_ids
    }
    result: list[ReviewFactFinding] = []
    for fact in facts:
        supporting = sorted(submitted.intersection(fact.supporting_evidence_ids))
        contradicting = sorted(submitted.intersection(fact.contradicting_evidence_ids))
        statements = sorted(appeared_statement_ids.intersection(fact.supporting_statement_ids))
        challenged = sorted(challenged_ids.intersection(supporting + contradicting))
        has_support = bool(supporting or statements)
        if has_support and (contradicting or challenged):
            status = FactFindingStatus.DISPUTED
        elif has_support:
            status = FactFindingStatus.SUPPORTED
        else:
            status = FactFindingStatus.INSUFFICIENT
        result.append(
            ReviewFactFinding(
                fact_id=fact.id,
                description=fact.description,
                status=status,
                submitted_supporting_evidence_ids=supporting,
                submitted_contradicting_evidence_ids=contradicting,
                appeared_statement_ids=statements,
                challenged_evidence_ids=challenged,
            )
        )
    return result


def _element_finding(
    element: Any,
    findings: dict[str, ReviewFactFinding],
    citations: dict[str, ReviewLegalCitation],
) -> ReviewElementFinding:
    supporting = [findings[item] for item in element.supporting_fact_ids]
    contradicting = [findings[item] for item in element.contradicting_fact_ids]
    if any(item.status is FactFindingStatus.DISPUTED for item in supporting + contradicting):
        status = ElementFindingStatus.DISPUTED
    elif any(item.status is FactFindingStatus.SUPPORTED for item in contradicting):
        status = ElementFindingStatus.NOT_SATISFIED
    elif supporting and all(item.status is FactFindingStatus.SUPPORTED for item in supporting):
        status = ElementFindingStatus.SATISFIED
    else:
        status = ElementFindingStatus.INSUFFICIENT
    return ReviewElementFinding(
        element_id=element.id,
        description=element.text,
        status=status,
        supporting_fact_ids=element.supporting_fact_ids,
        contradicting_fact_ids=element.contradicting_fact_ids,
        legal_source_ids=element.legal_source_ids,
        citations=[citations[item] for item in element.legal_source_ids],
    )


def _score_courtroom_learning(
    *,
    user_role: str,
    role_materials: list[RoleMaterial],
    evidence_submissions: list[Any],
    evidence_agenda: list[Any],
    required_source_ids: set[str],
    citations: dict[str, ReviewLegalCitation],
    fact_findings: list[ReviewFactFinding],
    element_findings: list[ReviewElementFinding],
) -> tuple[list[ReviewScoreDimension], list[ReviewRecommendation]]:
    """基于可追溯庭审记录评分，避免将教学评价交给自由生成模型。"""

    material = next((item for item in role_materials if item.role == user_role), None)
    priority_evidence_ids = set(material.priority_evidence_ids) if material is not None else set()
    submitted_by_user = {
        item.evidence_id for item in evidence_submissions if item.submitted_by == user_role
    }
    submitted_priority_ids = priority_evidence_ids.intersection(submitted_by_user)
    submission_score = _ratio_score(len(submitted_priority_ids), len(priority_evidence_ids))

    response_items = [item for item in evidence_agenda if item.responding_role == user_role]
    addressed_items = [item for item in response_items if item.status != "deferred"]
    response_score = _ratio_score(len(addressed_items), len(response_items))

    cited_source_ids = required_source_ids.intersection(citations)
    legal_score = _ratio_score(len(cited_source_ids), len(required_source_ids))

    applicable_elements = [
        item for item in element_findings if item.status is not ElementFindingStatus.NOT_APPLICABLE
    ]
    resolved_elements = [
        item
        for item in applicable_elements
        if item.status in {ElementFindingStatus.SATISFIED, ElementFindingStatus.NOT_SATISFIED}
    ]
    disputed_elements = [
        item for item in applicable_elements if item.status is ElementFindingStatus.DISPUTED
    ]
    # 有争议的要件仍形成了对抗材料，但尚未达到可清晰判断的程度，按半分计入闭合度。
    closure_score = _ratio_score(
        len(resolved_elements) * 2 + len(disputed_elements), len(applicable_elements) * 2
    )

    dimensions = [
        ReviewScoreDimension(
            key="priority_evidence_submission",
            label="优先证据提交",
            score=submission_score,
            numerator=len(submitted_priority_ids),
            denominator=len(priority_evidence_ids),
            summary=(
                "案件未配置本席位优先证据。"
                if not priority_evidence_ids
                else (
                    f"已提交 {len(submitted_priority_ids)}/{len(priority_evidence_ids)} 项"
                    "本席位优先证据。"
                )
            ),
        ),
        ReviewScoreDimension(
            key="opponent_evidence_response",
            label="对方证据回应",
            score=response_score,
            numerator=len(addressed_items),
            denominator=len(response_items),
            summary=(
                "本席位没有需要回应的对方证据。"
                if not response_items
                else f"已质证或明确无异议 {len(addressed_items)}/{len(response_items)} 项对方证据。"
            ),
        ),
        ReviewScoreDimension(
            key="legal_authority_coverage",
            label="法源覆盖",
            score=legal_score,
            numerator=len(cited_source_ids),
            denominator=len(required_source_ids),
            summary=f"复盘已核验 {len(cited_source_ids)}/{len(required_source_ids)} 项必要法源。",
        ),
        ReviewScoreDimension(
            key="issue_closure",
            label="争点闭合",
            score=closure_score,
            numerator=len(resolved_elements),
            denominator=len(applicable_elements),
            summary=(
                "没有适用的构成要件。"
                if not applicable_elements
                else (
                    f"已有 {len(resolved_elements)}/{len(applicable_elements)} 项构成要件"
                    "形成明确判断。"
                )
            ),
        ),
    ]
    return dimensions, _recommendations(
        priority_evidence_ids=priority_evidence_ids,
        submitted_priority_ids=submitted_priority_ids,
        response_items=response_items,
        fact_findings=fact_findings,
        element_findings=element_findings,
        cited_source_ids=cited_source_ids,
        required_source_ids=required_source_ids,
    )


def _turn_diagnostics(
    *,
    user_role: str,
    events: list[Any],
    evidence_fact_ids: dict[str, set[str]],
) -> list[ReviewTurnDiagnostic]:
    """只评价用户席位可验证的结构字段，不从自然语言猜测事实或证据引用。"""

    diagnostics: list[ReviewTurnDiagnostic] = []
    for event in events:
        if event.actor_role != user_role:
            continue
        payload = event.payload
        content = str(payload.get("content") or "").strip()
        evidence_ids = sorted(set(payload.get("evidence_ids") or []))
        fact_ids = sorted(
            {
                fact_id
                for evidence_id in evidence_ids
                for fact_id in evidence_fact_ids.get(evidence_id, set())
            }
        )
        checks: list[ReviewTurnCheck]
        recommendation: str | None = None
        if event.action == "make_statement":
            checks = [
                _turn_check("content", "形成完整陈述", bool(content), "陈述正文已写入庭审记录。"),
                _turn_check(
                    "evidence_anchor",
                    "绑定已提交证据",
                    bool(evidence_ids),
                    (
                        f"已绑定证据：{'、'.join(evidence_ids)}。"
                        if evidence_ids
                        else "本次陈述没有结构化证据锚点。"
                    ),
                ),
                _turn_check(
                    "fact_anchor",
                    "关联案卷事实",
                    bool(fact_ids),
                    (
                        f"所选证据关联事实：{'、'.join(fact_ids)}。"
                        if fact_ids
                        else "本次陈述尚不能从证据锚点确定关联事实。"
                    ),
                ),
            ]
            score = _turn_score(checks, [40, 35, 25])
            if not evidence_ids:
                recommendation = "后续陈述可勾选已经提交的证据，使观点能够定位到证据和事实。"
            elif not fact_ids:
                recommendation = "检查所选证据与待证事实的案卷关联，避免只有证据编号而无证明目的。"
        elif event.action == "challenge_evidence":
            dimensions = sorted(set(payload.get("challenge_dimensions") or []))
            checks = [
                _turn_check("content", "说明质证理由", bool(content), "质证意见已写入庭审记录。"),
                _turn_check(
                    "evidence_anchor",
                    "指向具体证据",
                    bool(evidence_ids),
                    f"质证对象：{'、'.join(evidence_ids) or '无'}。",
                ),
                _turn_check(
                    "challenge_dimension",
                    "明确质证维度",
                    bool(dimensions),
                    f"质证维度：{'、'.join(dimensions) or '无'}。",
                ),
            ]
            score = _turn_score(checks, [40, 30, 30])
            if score < 100:
                recommendation = "质证应同时明确证据对象、真实性或合法性等维度，并说明具体理由。"
        elif event.action == "question_participant":
            target_id = str(payload.get("target_id") or "")
            checks = [
                _turn_check("content", "提出明确问题", bool(content), "发问正文已写入庭审记录。"),
                _turn_check(
                    "participant_target",
                    "指向庭审参与人",
                    bool(target_id),
                    f"询问对象：{target_id or '无'}。",
                ),
            ]
            score = _turn_score(checks, [60, 40])
            if score < 100:
                recommendation = "发问应指向明确参与人，并保持一个问题对应一个待核实事项。"
        else:
            continue
        diagnostics.append(
            ReviewTurnDiagnostic(
                event_sequence_number=event.sequence_number,
                actor_role=event.actor_role,
                phase=event.phase,
                action=event.action,
                score=score,
                evidence_ids=evidence_ids,
                fact_ids=fact_ids,
                checks=checks,
                recommendation=recommendation,
            )
        )
    return diagnostics


def _turn_check(key: str, label: str, passed: bool, detail: str) -> ReviewTurnCheck:
    return ReviewTurnCheck(key=key, label=label, passed=passed, detail=detail)


def _turn_score(checks: list[ReviewTurnCheck], weights: list[int]) -> int:
    if len(checks) != len(weights) or sum(weights) != 100:
        raise ValueError("turn diagnostic weights must align with checks and total 100")
    return sum(weight for check, weight in zip(checks, weights, strict=True) if check.passed)


def _ratio_score(numerator: int, denominator: int) -> int:
    """没有评价样本时按满分处理，避免将不适用的要求计为失分。"""

    return 100 if denominator == 0 else round(numerator * 100 / denominator)


def _weighted_total(dimensions: list[ReviewScoreDimension]) -> int:
    weights = {
        "priority_evidence_submission": 30,
        "opponent_evidence_response": 30,
        "legal_authority_coverage": 20,
        "issue_closure": 20,
    }
    return round(sum(item.score * weights[item.key] for item in dimensions) / 100)


def _recommendations(
    *,
    priority_evidence_ids: set[str],
    submitted_priority_ids: set[str],
    response_items: list[Any],
    fact_findings: list[ReviewFactFinding],
    element_findings: list[ReviewElementFinding],
    cited_source_ids: set[str],
    required_source_ids: set[str],
) -> list[ReviewRecommendation]:
    """建议只引用会话中已有的证据、事实或要件标识，便于教师定位。"""

    recommendations: list[ReviewRecommendation] = []
    missing_priority_ids = sorted(priority_evidence_ids - submitted_priority_ids)
    if missing_priority_ids:
        recommendations.append(
            ReviewRecommendation(
                id="missing-priority-evidence",
                priority="high",
                title="补足本席位优先证据",
                detail=(
                    f"尚有 {len(missing_priority_ids)} 项优先证据未提交，"
                    "后续演练应先说明其证明目的。"
                ),
                related_evidence_ids=missing_priority_ids,
            )
        )
    deferred_ids = sorted(item.evidence_id for item in response_items if item.status == "deferred")
    if deferred_ids:
        recommendations.append(
            ReviewRecommendation(
                id="deferred-opponent-evidence",
                priority="high",
                title="逐项回应暂缓证据",
                detail=f"有 {len(deferred_ids)} 项对方证据在结束阶段前未质证或表示无异议。",
                related_evidence_ids=deferred_ids,
            )
        )
    weak_fact_ids = sorted(
        item.fact_id
        for item in fact_findings
        if item.status in {FactFindingStatus.DISPUTED, FactFindingStatus.INSUFFICIENT}
    )
    if weak_fact_ids:
        recommendations.append(
            ReviewRecommendation(
                id="unresolved-facts",
                priority="medium",
                title="围绕未闭合事实补强攻防",
                detail=(
                    f"{len(weak_fact_ids)} 项事实仍存在争议或证据不足，"
                    "应结合证据关联性和证明力继续说明。"
                ),
                related_fact_ids=weak_fact_ids,
            )
        )
    weak_element_ids = sorted(
        item.element_id
        for item in element_findings
        if item.status in {ElementFindingStatus.DISPUTED, ElementFindingStatus.INSUFFICIENT}
    )
    if weak_element_ids:
        recommendations.append(
            ReviewRecommendation(
                id="unclosed-legal-elements",
                priority="medium",
                title="围绕构成要件组织论证",
                detail=(
                    f"{len(weak_element_ids)} 项构成要件尚未形成明确判断，"
                    "应将事实主张与对应法源逐项连接。"
                ),
                related_element_ids=weak_element_ids,
            )
        )
    missing_source_ids = sorted(required_source_ids - cited_source_ids)
    if missing_source_ids:
        recommendations.append(
            ReviewRecommendation(
                id="missing-legal-authority",
                priority="high",
                title="补足必要法源",
                detail="复盘引用未覆盖全部必要法源，不能据此形成法律适用判断。",
            )
        )
    return recommendations
