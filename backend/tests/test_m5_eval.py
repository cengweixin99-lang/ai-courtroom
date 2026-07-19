from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.core.config import Settings
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.legal_search import (
    LegalRetrievalMode,
    LegalSearchHit,
    load_legal_source_manifest,
)
from mootcourt.schemas.m5_eval import M5Subset, load_m5_eval_datasets
from mootcourt.services.case_importer import import_case_package
from mootcourt.services.m5_eval import _metric_checks, evaluate_m5_suite

MANIFEST = Path(__file__).parents[2] / "evals" / "m5_manifest.json"
CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"
LEGAL_MANIFEST = Path(__file__).parents[2] / "knowledge" / "legal" / "source_manifest.json"


class DatasetLegalSearchRepository:
    def __init__(self) -> None:
        _, documents = load_legal_source_manifest(LEGAL_MANIFEST)
        self._documents = {item.source_id: item for item in documents}
        datasets = load_m5_eval_datasets(MANIFEST)
        legal = __import__("json").loads(datasets.legal_path.read_text(encoding="utf-8"))
        self._expected = {
            item["query"]: item["expected_relevant_source_ids"] for item in legal["cases"]
        }

    async def search(self, **kwargs: Any) -> list[LegalSearchHit]:
        source_ids = self._expected[str(kwargs["query"])]
        return [
            LegalSearchHit(
                source_id=source_id,
                instrument_title=self._documents[source_id].instrument_title,
                article_number=self._documents[source_id].article_number,
                text=self._documents[source_id].text,
                jurisdiction=self._documents[source_id].jurisdiction,
                effective_from=self._documents[source_id].effective_from,
                effective_to=self._documents[source_id].effective_to,
                status=self._documents[source_id].status,
                review_status=self._documents[source_id].review_status,
                authority_level=self._documents[source_id].authority_level,
                official_source_url=self._documents[source_id].official_source_url,
                version_hash=self._documents[source_id].version_hash,
                score=float(10 - index),
                retrieval_mode=LegalRetrievalMode.BM25,
                bm25_score=float(10 - index),
                bm25_rank=index,
            )
            for index, source_id in enumerate(source_ids, start=1)
        ]


def test_m5_datasets_meet_prd_subset_counts() -> None:
    datasets = load_m5_eval_datasets(MANIFEST)

    assert len(datasets.procedure.cases) == 15
    assert len(datasets.participants.cases) == 10
    assert len(datasets.end_to_end.cases) == 5
    assert datasets.legal_path.name == "bm25_baseline_cases.json"
    assert datasets.manifest.suite == "mootcourt_mvp_m5"


def test_m5_case_ids_are_unique_across_non_legal_subsets() -> None:
    datasets = load_m5_eval_datasets(MANIFEST)
    ids = [
        *[item.id for item in datasets.procedure.cases],
        *[item.id for item in datasets.participants.cases],
        *[item.id for item in datasets.end_to_end.cases],
    ]

    assert len(ids) == len(set(ids)) == 30
    assert datasets.procedure.dataset is M5Subset.PROCEDURE_PERMISSIONS


def test_m5_dataset_loader_rejects_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing Eval dataset"):
        load_m5_eval_datasets(tmp_path / "missing.json")


def test_m5_at_least_thresholds_are_not_treated_as_zero_percent() -> None:
    from types import SimpleNamespace

    from mootcourt.schemas.m5_eval import EvalCaseResult

    def result(identifier: str, subset: M5Subset, metric: str) -> EvalCaseResult:
        return EvalCaseResult(
            id=identifier,
            subset=subset,
            metric=metric,
            expected={},
            actual={
                "leaked_tokens": [],
                "element_count": 6,
                "all_elements_cited": True,
            },
            passed=True,
            failures=[],
            latency_ms=1,
        )

    results = [
        result("P1", M5Subset.PROCEDURE_PERMISSIONS, "nonexistent_evidence"),
        result("P2", M5Subset.PROCEDURE_PERMISSIONS, "illegal_stage"),
        result("A1", M5Subset.PARTICIPANT_BOUNDARIES, "participant_boundary"),
        result("E1", M5Subset.END_TO_END, "end_to_end_completion"),
    ]
    legal = SimpleNamespace(
        cases=[SimpleNamespace(passed=True)],
        metrics=SimpleNamespace(
            recall_at_k=1.0,
            precision_at_k=0.8,
            validity_filter_accuracy=1.0,
            refusal_accuracy=1.0,
        ),
    )

    checks = _metric_checks(results, legal)

    assert all(item.passed for item in checks)


async def test_m5_runner_executes_all_fifty_cases(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    report = await evaluate_m5_suite(
        session_factory,
        DatasetLegalSearchRepository(),
        load_m5_eval_datasets(MANIFEST),
        Settings(llm_provider="fake", llm_model=""),
        "mock-legal-index-v1",
    )

    assert report.passed
    assert report.total_case_count == 50
    assert len(report.cases) == 30
    assert len(report.legal_report.cases) == 20
    assert report.subset_counts == {
        M5Subset.PROCEDURE_PERMISSIONS: 15,
        M5Subset.PARTICIPANT_BOUNDARIES: 10,
        M5Subset.LEGAL_RAG: 20,
        M5Subset.END_TO_END: 5,
    }
    assert all(item.passed for item in report.cases)
    assert all(item.passed for item in report.checks)
    assert report.cost.agent_call_count == 12
    assert report.cost.input_tokens == 1_440
    assert report.cost.output_tokens == 480
