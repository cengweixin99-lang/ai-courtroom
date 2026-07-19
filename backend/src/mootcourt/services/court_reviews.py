from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mootcourt.domain.courtroom import CourtPhase, Role
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import FactRecord, LegalProfile
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
    facts = [FactRecord.model_validate(item.payload) for item in package.facts]
    fact_findings = _fact_findings(facts, submitted, statement_traces, procedural_requests)
    findings_by_id = {item.fact_id: item for item in fact_findings}
    element_findings = [
        _element_finding(element, findings_by_id, citations) for element in legal_config.elements
    ]
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
        fact_findings=fact_findings,
        element_findings=element_findings,
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
