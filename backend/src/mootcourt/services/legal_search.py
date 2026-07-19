from __future__ import annotations

from time import perf_counter

from mootcourt.repositories.legal_search import LegalSearchRepository
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import LegalProfile
from mootcourt.schemas.legal_search import (
    LegalRetrievalMode,
    LegalSearchFilters,
    LegalSearchOutcome,
    LegalSearchRequest,
    LegalSearchResponse,
)
from mootcourt.search.embeddings import EmbeddingProvider

APPROVED_REVIEW_STATUSES = ("verified",)


async def search_case_law(
    unit_of_work: SqlAlchemyUnitOfWork,
    search_repository: LegalSearchRepository,
    request: LegalSearchRequest,
    embedding_provider: EmbeddingProvider | None = None,
) -> LegalSearchResponse | None:
    started_at = perf_counter()
    package = await unit_of_work.case_packages.get_runtime_package(
        request.case_id, request.package_version
    )
    if package is None:
        return None
    profile = LegalProfile.model_validate(package.legal_profile)
    allowed_source_ids = sorted(
        set(
            profile.substantive_source_ids
            + profile.procedure_source_ids
            + profile.evidence_rule_source_ids
        )
    )
    source_jurisdictions = {
        str(item.payload.get("jurisdiction"))
        for item in package.legal_sources
        if item.source_id in allowed_source_ids and item.payload.get("jurisdiction")
    }
    if len(source_jurisdictions) != 1:
        # 一个案件版本只能绑定一个规范化法源法域，否则检索过滤将失去确定性。
        raise ValueError("LegalProfile sources must resolve to exactly one jurisdiction")
    source_jurisdiction = source_jurisdictions.pop()
    # 查询只来自单一法律问题，过滤条件由案件 LegalProfile 决定，客户端不能放宽法域或日期。
    query_embedding = None
    embedding_version = None
    if embedding_provider is not None:
        vectors = await embedding_provider.embed([request.query])
        if len(vectors) != 1:
            raise ValueError("embedding provider must return exactly one query vector")
        query_embedding = vectors[0]
        embedding_version = embedding_provider.version
    hits = await search_repository.search(
        query=request.query,
        jurisdiction=source_jurisdiction,
        law_as_of_date=profile.law_as_of_date,
        allowed_source_ids=allowed_source_ids,
        approved_review_statuses=APPROVED_REVIEW_STATUSES,
        size=request.top_k,
        query_embedding=query_embedding,
        embedding_version=embedding_version,
    )
    outcome = (
        LegalSearchOutcome.SUFFICIENT_LEGAL_AUTHORITY
        if hits
        else LegalSearchOutcome.INSUFFICIENT_LEGAL_AUTHORITY
    )
    retrieval_mode = (
        LegalRetrievalMode.HYBRID_RRF if embedding_provider is not None else LegalRetrievalMode.BM25
    )
    filters = LegalSearchFilters(
        jurisdiction=source_jurisdiction,
        law_as_of_date=profile.law_as_of_date,
        allowed_source_ids=allowed_source_ids,
        approved_review_statuses=list(APPROVED_REVIEW_STATUSES),
        top_k=request.top_k,
    )
    latency_ms = round((perf_counter() - started_at) * 1000)
    # Trace 与业务响应在同一事务写入；API 失败回滚时不会留下误导性的成功审计记录。
    trace = await unit_of_work.legal_search_traces.add(
        package_id=package.id,
        legal_profile_id=profile.id,
        query=request.query,
        retrieval_mode=retrieval_mode.value,
        embedding_version=embedding_version,
        outcome=outcome.value,
        filters=filters.model_dump(mode="json"),
        hits=[item.model_dump(mode="json") for item in hits],
        latency_ms=latency_ms,
    )
    return LegalSearchResponse(
        trace_id=trace.id,
        outcome=outcome,
        legal_profile_id=profile.id,
        jurisdiction=profile.jurisdiction,
        law_as_of_date=profile.law_as_of_date,
        query=request.query,
        hits=hits,
        retrieval_mode=retrieval_mode,
        embedding_version=embedding_version,
    )
