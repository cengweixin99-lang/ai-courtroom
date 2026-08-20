from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mootcourt.cli import compare_legal_evals as compare_cli
from mootcourt.schemas.eval.legal_eval import (
    LegalEvalReport,
    load_legal_eval_admission_policy,
    load_legal_eval_report,
)
from mootcourt.services.legal_eval_comparison import compare_legal_eval_reports

BASELINE = (
    Path(__file__).parents[2] / "evals" / "legal_rag" / "results" / "bm25_baseline_report.json"
)
POLICY = Path(__file__).parents[2] / "evals" / "legal_rag" / "hybrid_admission_policy.json"


def _candidate(**metric_updates: float) -> LegalEvalReport:
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    raw["retrieval_mode"] = "hybrid_rrf"
    raw["embedding_version"] = "ollama-bge-m3-790764642607-1024-v1"
    raw["metrics"].update(metric_updates)
    return LegalEvalReport.model_validate(raw)


def test_hybrid_candidate_is_admitted_when_all_checks_pass() -> None:
    baseline = load_legal_eval_report(BASELINE)
    candidate = _candidate(mean_reciprocal_rank=0.95)

    comparison = compare_legal_eval_reports(
        baseline,
        candidate,
        load_legal_eval_admission_policy(POLICY),
        baseline_path=BASELINE,
        candidate_path=Path("hybrid.json"),
    )

    assert comparison.admitted is True
    assert all(check.passed for check in comparison.checks)
    assert comparison.mean_reciprocal_rank.delta == pytest.approx(0.0125)


def test_hybrid_candidate_is_rejected_on_recall_and_safety_regression() -> None:
    baseline = load_legal_eval_report(BASELINE)
    candidate = _candidate(
        recall_at_k=0.95,
        validity_filter_accuracy=0.95,
        refusal_accuracy=0.75,
    )

    comparison = compare_legal_eval_reports(
        baseline,
        candidate,
        load_legal_eval_admission_policy(POLICY),
        baseline_path=BASELINE,
        candidate_path=Path("hybrid.json"),
    )

    failed = {check.name for check in comparison.checks if not check.passed}
    assert comparison.admitted is False
    assert {"recall_at_k", "validity_filter_accuracy", "refusal_accuracy"} <= failed


def test_compare_cli_exits_nonzero_for_bm25_report_used_as_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mootcourt-compare-legal-evals",
            str(BASELINE),
            str(BASELINE),
            "--policy",
            str(POLICY),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        compare_cli.main()

    assert exc_info.value.code == 1


def test_compare_cli_writes_admission_report(tmp_path: Path) -> None:
    candidate_path = tmp_path / "hybrid.json"
    candidate_path.write_text(_candidate().model_dump_json(), encoding="utf-8")
    output_path = tmp_path / "results" / "comparison.json"

    admitted = compare_cli._run(BASELINE, candidate_path, POLICY, output_path)

    assert admitted is True
    assert json.loads(output_path.read_text(encoding="utf-8"))["admitted"] is True
