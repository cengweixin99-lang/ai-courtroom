from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.cli import eval_legal as eval_legal_cli
from mootcourt.core.config import Settings
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.eval.legal_eval import LegalEvalDataset, LegalEvalReport, load_legal_eval_dataset
from mootcourt.schemas.legal_search import LegalSearchHit
from mootcourt.services.case_importer import import_case_package
from mootcourt.services.legal_eval import evaluate_legal_retrieval

EVAL_DATASET = Path(__file__).parents[2] / "evals" / "legal_rag" / "bm25_baseline_cases.json"
CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"


class QueryLegalSearchRepository:
    def __init__(self, hits_by_query: dict[str, list[LegalSearchHit]]) -> None:
        self.hits_by_query = hits_by_query

    async def search(self, **kwargs: Any) -> list[LegalSearchHit]:
        return self.hits_by_query.get(str(kwargs["query"]), [])[: int(kwargs["size"])]


def _hit(source_id: str) -> LegalSearchHit:
    return LegalSearchHit(
        source_id=source_id,
        instrument_title="测试法源",
        article_number="第一条",
        text="测试条款文本",
        jurisdiction="PRC",
        effective_from=date(2021, 3, 1),
        effective_to=None,
        status="effective",
        review_status="verified",
        authority_level="law_current_official",
        official_source_url="https://flk.npc.gov.cn/",
        version_hash="a" * 64,
        score=1,
    )


def test_legal_eval_dataset_contains_prd_minimum_cases() -> None:
    dataset = load_legal_eval_dataset(EVAL_DATASET)

    assert len(dataset.cases) >= 20
    assert dataset.top_k == 5
    assert dataset.thresholds.recall_at_k == 0.9
    assert dataset.thresholds.precision_at_k == 0.7
    assert dataset.thresholds.validity_filter_accuracy == 1
    assert dataset.thresholds.refusal_accuracy == 0.95
    assert all(case.forbidden_source_ids for case in dataset.cases)


def test_legal_eval_dataset_rejects_conflicting_labels(tmp_path: Path) -> None:
    raw = json.loads(EVAL_DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["forbidden_source_ids"] = raw["cases"][0]["expected_relevant_source_ids"]
    path = tmp_path / "invalid_eval.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="both relevant and forbidden"):
        load_legal_eval_dataset(path)


async def test_eval_report_calculates_metrics_and_passes_thresholds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dataset = LegalEvalDataset.model_validate(
        {
            "dataset": "unit-test",
            "version": "1.0.0",
            "index_version": "v1",
            "top_k": 5,
            "thresholds": {
                "recall_at_k": 1,
                "precision_at_k": 0.5,
                "validity_filter_accuracy": 1,
                "refusal_accuracy": 1,
            },
            "cases": [
                {
                    "id": "positive",
                    "description": "命中必要法源",
                    "category": "retrieval",
                    "query": "盗窃罪如何处罚",
                    "case_id": "CASE-001",
                    "expected_outcome": "SUFFICIENT_LEGAL_AUTHORITY",
                    "expected_relevant_source_ids": ["LS-CRIMINAL-LAW-264"],
                    "forbidden_source_ids": ["LS-CRIMINAL-LAW-264-2009-HISTORICAL"],
                },
                {
                    "id": "refusal",
                    "description": "无依据时拒答",
                    "category": "refusal",
                    "query": "火星殖民地税法",
                    "case_id": "CASE-001",
                    "expected_outcome": "INSUFFICIENT_LEGAL_AUTHORITY",
                    "expected_relevant_source_ids": [],
                    "forbidden_source_ids": ["LS-CRIMINAL-LAW-264-2009-HISTORICAL"],
                },
            ],
        }
    )
    repository = QueryLegalSearchRepository(
        {
            "盗窃罪如何处罚": [
                _hit("LS-CRIMINAL-LAW-264"),
                _hit("LS-CRIMINAL-LAW-13"),
            ]
        }
    )
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

        report = await evaluate_legal_retrieval(
            unit_of_work, repository, dataset, "mootcourt-legal-articles-v1"
        )

    assert report.metrics.recall_at_k == 1
    assert report.metrics.precision_at_k == 0.5
    assert report.metrics.mean_reciprocal_rank == 1
    assert report.metrics.validity_filter_accuracy == 1
    assert report.metrics.refusal_accuracy == 1
    assert report.passed is True


async def test_eval_report_records_failures_and_fails_gate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dataset = LegalEvalDataset.model_validate(
        {
            "dataset": "unit-test-failure",
            "version": "1.0.0",
            "index_version": "v1",
            "cases": [
                {
                    "id": "leaked-history",
                    "description": "历史版本泄漏",
                    "category": "version_filter",
                    "query": "盗窃罪第二百六十四条",
                    "case_id": "CASE-001",
                    "expected_outcome": "SUFFICIENT_LEGAL_AUTHORITY",
                    "expected_relevant_source_ids": ["LS-CRIMINAL-LAW-264"],
                    "forbidden_source_ids": ["LS-CRIMINAL-LAW-264-2009-HISTORICAL"],
                }
            ],
        }
    )
    repository = QueryLegalSearchRepository(
        {"盗窃罪第二百六十四条": [_hit("LS-CRIMINAL-LAW-264-2009-HISTORICAL")]}
    )
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

        report = await evaluate_legal_retrieval(
            unit_of_work, repository, dataset, "mootcourt-legal-articles-v1"
        )

    result = report.cases[0]
    assert result.failures == [
        "MISSING_RELEVANT_SOURCE",
        "FORBIDDEN_SOURCE_RETRIEVED",
    ]
    assert report.metrics.recall_at_k == 0
    assert report.metrics.validity_filter_accuracy == 0
    assert report.passed is False


async def test_eval_cli_writes_reproducible_report(
    tmp_path: Path,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = LegalEvalReport.model_validate(
        {
            "dataset": "cli-test",
            "dataset_version": "1.0.0",
            "index_name": "mootcourt-legal-articles-v1",
            "top_k": 5,
            "thresholds": {},
            "metrics": {
                "case_count": 1,
                "positive_case_count": 0,
                "refusal_case_count": 1,
                "validity_filter_case_count": 1,
                "recall_at_k": 1,
                "precision_at_k": 1,
                "mean_reciprocal_rank": 1,
                "validity_filter_accuracy": 1,
                "refusal_accuracy": 1,
            },
            "cases": [],
            "passed": True,
        }
    )

    async def fake_evaluate(*args: Any, **kwargs: Any) -> LegalEvalReport:
        return report

    async def fake_dispose() -> None:
        return None

    monkeypatch.setattr(
        eval_legal_cli,
        "get_settings",
        lambda: Settings(elasticsearch_index_prefix="mootcourt"),
    )
    monkeypatch.setattr(eval_legal_cli, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(eval_legal_cli, "get_elasticsearch_client", lambda: object())
    monkeypatch.setattr(eval_legal_cli, "evaluate_legal_retrieval", fake_evaluate)
    monkeypatch.setattr(eval_legal_cli, "dispose_elasticsearch_client", fake_dispose)
    monkeypatch.setattr(eval_legal_cli, "dispose_engine", fake_dispose)
    output_path = tmp_path / "nested" / "report.json"

    passed = await eval_legal_cli._run(EVAL_DATASET, output_path)

    assert passed is True
    assert json.loads(output_path.read_text(encoding="utf-8"))["dataset"] == "cli-test"


def test_eval_cli_exits_nonzero_when_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(dataset_path: Path, output_path: Path | None) -> bool:
        return False

    monkeypatch.setattr(eval_legal_cli, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["mootcourt-eval-legal", str(EVAL_DATASET)])

    with pytest.raises(SystemExit) as exc_info:
        eval_legal_cli.main()

    assert exc_info.value.code == 1
