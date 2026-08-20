from __future__ import annotations

from time import perf_counter

from mootcourt.repositories.legal_search import LegalSearchRepository
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.eval.legal_eval import (
    LegalEvalCase,
    LegalEvalCaseResult,
    LegalEvalDataset,
    LegalEvalFailure,
    LegalEvalMetrics,
    LegalEvalReport,
)
from mootcourt.schemas.legal_search import LegalSearchHit, LegalSearchOutcome, LegalSearchRequest
from mootcourt.search.embeddings import EmbeddingProvider
from mootcourt.services.legal_search import search_case_law


async def evaluate_legal_retrieval(
    unit_of_work: SqlAlchemyUnitOfWork,
    search_repository: LegalSearchRepository,
    dataset: LegalEvalDataset,
    index_name: str,
    embedding_provider: EmbeddingProvider | None = None,
) -> LegalEvalReport:
    results: list[LegalEvalCaseResult] = []
    for eval_case in dataset.cases:
        started_at = perf_counter()
        response = await search_case_law(
            unit_of_work,
            search_repository,
            LegalSearchRequest(
                case_id=eval_case.case_id,
                package_version=eval_case.package_version,
                query=eval_case.query,
                top_k=dataset.top_k,
            ),
            embedding_provider,
        )
        if response is None:
            raise ValueError(f"eval case references an unknown case package: {eval_case.case_id}")
        latency_ms = (perf_counter() - started_at) * 1000
        results.append(
            _evaluate_case(
                eval_case, response.outcome, response.hits, latency_ms, response.trace_id
            )
        )

    metrics = _calculate_metrics(results)
    thresholds = dataset.thresholds
    # 所有门槛都单独判断，避免高召回率掩盖历史版本泄漏或错误拒答。
    passed = (
        metrics.recall_at_k >= thresholds.recall_at_k
        and metrics.precision_at_k >= thresholds.precision_at_k
        and metrics.validity_filter_accuracy >= thresholds.validity_filter_accuracy
        and metrics.refusal_accuracy >= thresholds.refusal_accuracy
    )
    return LegalEvalReport(
        dataset=dataset.dataset,
        dataset_version=dataset.version,
        index_name=index_name,
        top_k=dataset.top_k,
        thresholds=thresholds,
        metrics=metrics,
        cases=results,
        passed=passed,
        retrieval_mode="hybrid_rrf" if embedding_provider is not None else "bm25",
        embedding_version=(embedding_provider.version if embedding_provider is not None else None),
    )


def _evaluate_case(
    eval_case: LegalEvalCase,
    actual_outcome: LegalSearchOutcome,
    hits: list[LegalSearchHit],
    latency_ms: float,
    trace_id: str | None = None,
) -> LegalEvalCaseResult:
    retrieved_ids = [hit.source_id for hit in hits]
    relevant_ids = set(eval_case.expected_relevant_source_ids)
    forbidden_ids = set(eval_case.forbidden_source_ids)
    retrieved_relevant = relevant_ids.intersection(retrieved_ids)
    failures: list[LegalEvalFailure] = []
    if actual_outcome != eval_case.expected_outcome:
        failures.append(LegalEvalFailure.OUTCOME_MISMATCH)
    if relevant_ids - set(retrieved_ids):
        failures.append(LegalEvalFailure.MISSING_RELEVANT_SOURCE)
    if forbidden_ids.intersection(retrieved_ids):
        failures.append(LegalEvalFailure.FORBIDDEN_SOURCE_RETRIEVED)

    positive = bool(relevant_ids)
    reciprocal_rank = None
    if positive:
        reciprocal_rank = next(
            (
                1 / rank
                for rank, source_id in enumerate(retrieved_ids, start=1)
                if source_id in relevant_ids
            ),
            0.0,
        )
    return LegalEvalCaseResult(
        id=eval_case.id,
        category=eval_case.category,
        query=eval_case.query,
        expected_outcome=eval_case.expected_outcome,
        actual_outcome=actual_outcome,
        expected_relevant_source_ids=eval_case.expected_relevant_source_ids,
        forbidden_source_ids=eval_case.forbidden_source_ids,
        retrieved_source_ids=retrieved_ids,
        retrieved_hits=hits,
        recall_at_k=len(retrieved_relevant) / len(relevant_ids) if positive else None,
        precision_at_k=len(retrieved_relevant) / len(retrieved_ids)
        if positive and retrieved_ids
        else 0,
        reciprocal_rank=reciprocal_rank,
        latency_ms=latency_ms,
        failures=failures,
        passed=not failures,
        trace_id=trace_id,
    )


def _calculate_metrics(results: list[LegalEvalCaseResult]) -> LegalEvalMetrics:
    positive = [item for item in results if item.recall_at_k is not None]
    refusals = [
        item
        for item in results
        if item.expected_outcome == LegalSearchOutcome.INSUFFICIENT_LEGAL_AUTHORITY
    ]
    validity_filters = [item for item in results if item.forbidden_source_ids]
    return LegalEvalMetrics(
        case_count=len(results),
        positive_case_count=len(positive),
        refusal_case_count=len(refusals),
        validity_filter_case_count=len(validity_filters),
        recall_at_k=_mean([item.recall_at_k for item in positive]),
        precision_at_k=_mean([item.precision_at_k for item in positive]),
        mean_reciprocal_rank=_mean([item.reciprocal_rank for item in positive]),
        validity_filter_accuracy=_ratio(
            sum(
                LegalEvalFailure.FORBIDDEN_SOURCE_RETRIEVED not in item.failures
                for item in validity_filters
            ),
            len(validity_filters),
        ),
        refusal_accuracy=_ratio(
            sum(
                item.actual_outcome == LegalSearchOutcome.INSUFFICIENT_LEGAL_AUTHORITY
                for item in refusals
            ),
            len(refusals),
        ),
    )


def _mean(values: list[float | None]) -> float:
    concrete = [value for value in values if value is not None]
    return sum(concrete) / len(concrete) if concrete else 1.0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
