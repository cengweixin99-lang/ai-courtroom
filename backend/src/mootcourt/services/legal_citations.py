from __future__ import annotations

from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.legal_search import (
    LegalCitation,
    LegalCitationFailure,
    LegalCitationValidationItem,
    LegalCitationValidationRequest,
    LegalCitationValidationResponse,
    LegalSearchHit,
    LegalSearchTraceView,
)


async def get_legal_search_trace(
    unit_of_work: SqlAlchemyUnitOfWork, trace_id: str
) -> LegalSearchTraceView | None:
    trace = await unit_of_work.legal_search_traces.get(trace_id)
    if trace is None:
        return None
    package = await unit_of_work.case_packages.get_by_database_id(trace.package_id)
    if package is None:
        return None
    return LegalSearchTraceView.model_validate(
        {
            "id": trace.id,
            "case_id": package.case_id,
            "package_version": package.package_version,
            "legal_profile_id": trace.legal_profile_id,
            "query": trace.query,
            "retrieval_mode": trace.retrieval_mode,
            "embedding_version": trace.embedding_version,
            "outcome": trace.outcome,
            "filters": trace.filters,
            "hits": trace.hits,
            "latency_ms": trace.latency_ms,
            "created_at": trace.created_at,
        }
    )


async def validate_legal_citations(
    unit_of_work: SqlAlchemyUnitOfWork,
    request: LegalCitationValidationRequest,
) -> LegalCitationValidationResponse | None:
    trace = await get_legal_search_trace(unit_of_work, request.trace_id)
    if trace is None:
        return None
    hits_by_id = {item.source_id: item for item in trace.hits}
    results = [
        _validate_citation(item, hits_by_id.get(item.source_id)) for item in request.citations
    ]
    return LegalCitationValidationResponse(
        trace_id=request.trace_id,
        valid=all(item.valid for item in results),
        results=results,
    )


def _validate_citation(
    citation: LegalCitation, hit: LegalSearchHit | None
) -> LegalCitationValidationItem:
    if hit is None:
        return LegalCitationValidationItem(
            source_id=citation.source_id,
            valid=False,
            failures=[LegalCitationFailure.SOURCE_NOT_RETRIEVED],
        )
    failures: list[LegalCitationFailure] = []
    # 法条原文和版本元数据必须与审计快照逐字段一致，禁止摘要改写冒充直接引用。
    if citation.article_number != hit.article_number:
        failures.append(LegalCitationFailure.ARTICLE_NUMBER_MISMATCH)
    if citation.text != hit.text:
        failures.append(LegalCitationFailure.TEXT_MISMATCH)
    if citation.official_source_url != hit.official_source_url:
        failures.append(LegalCitationFailure.OFFICIAL_SOURCE_MISMATCH)
    if citation.version_hash != hit.version_hash:
        failures.append(LegalCitationFailure.VERSION_HASH_MISMATCH)
    return LegalCitationValidationItem(
        source_id=citation.source_id,
        valid=not failures,
        failures=failures,
    )
