from __future__ import annotations

from pathlib import Path

from mootcourt.schemas.eval.legal_eval import (
    LegalEvalAdmissionCheck,
    LegalEvalAdmissionPolicy,
    LegalEvalComparisonReport,
    LegalEvalMetricDelta,
    LegalEvalReport,
)


def compare_legal_eval_reports(
    baseline: LegalEvalReport,
    candidate: LegalEvalReport,
    policy: LegalEvalAdmissionPolicy,
    *,
    baseline_path: Path,
    candidate_path: Path,
) -> LegalEvalComparisonReport:
    checks: list[LegalEvalAdmissionCheck] = []
    _check(
        checks,
        "retrieval_modes",
        baseline.retrieval_mode == policy.required_baseline_mode
        and candidate.retrieval_mode == policy.required_candidate_mode,
        f"expected {policy.required_baseline_mode} -> {policy.required_candidate_mode}",
    )
    _check(
        checks,
        "candidate_embedding_version",
        bool(candidate.embedding_version),
        "candidate report must freeze a non-empty embedding version",
    )
    if policy.require_same_dataset:
        _check(
            checks,
            "same_dataset",
            baseline.dataset == candidate.dataset
            and baseline.dataset_version == candidate.dataset_version
            and baseline.top_k == candidate.top_k,
            "dataset, dataset version, and top_k must match",
        )
    if policy.require_same_case_set:
        _check(
            checks,
            "same_case_set",
            {item.id for item in baseline.cases} == {item.id for item in candidate.cases},
            "baseline and candidate must contain the same Eval case IDs",
        )
    if policy.require_candidate_eval_pass:
        _check(
            checks,
            "candidate_eval_gate",
            candidate.passed,
            "candidate must pass its standalone PRD thresholds",
        )

    baseline_metrics = baseline.metrics
    candidate_metrics = candidate.metrics
    _minimum_and_regression_check(
        checks,
        "recall_at_k",
        baseline_metrics.recall_at_k,
        candidate_metrics.recall_at_k,
        policy.minimum_recall_at_k,
        policy.maximum_recall_regression,
    )
    _minimum_and_regression_check(
        checks,
        "precision_at_k",
        baseline_metrics.precision_at_k,
        candidate_metrics.precision_at_k,
        policy.minimum_precision_at_k,
        policy.maximum_precision_regression,
    )
    _minimum_and_regression_check(
        checks,
        "mean_reciprocal_rank",
        baseline_metrics.mean_reciprocal_rank,
        candidate_metrics.mean_reciprocal_rank,
        policy.minimum_mean_reciprocal_rank,
        policy.maximum_mrr_regression,
    )
    _check(
        checks,
        "validity_filter_accuracy",
        candidate_metrics.validity_filter_accuracy >= policy.minimum_validity_filter_accuracy,
        f"candidate must be >= {policy.minimum_validity_filter_accuracy:.4f}",
    )
    _check(
        checks,
        "refusal_accuracy",
        candidate_metrics.refusal_accuracy >= policy.minimum_refusal_accuracy,
        f"candidate must be >= {policy.minimum_refusal_accuracy:.4f}",
    )
    return LegalEvalComparisonReport(
        policy_id=policy.policy_id,
        policy_version=policy.version,
        dataset=baseline.dataset,
        baseline_report=str(baseline_path),
        candidate_report=str(candidate_path),
        candidate_embedding_version=candidate.embedding_version or "missing",
        recall_at_k=_delta(baseline_metrics.recall_at_k, candidate_metrics.recall_at_k),
        precision_at_k=_delta(baseline_metrics.precision_at_k, candidate_metrics.precision_at_k),
        mean_reciprocal_rank=_delta(
            baseline_metrics.mean_reciprocal_rank,
            candidate_metrics.mean_reciprocal_rank,
        ),
        validity_filter_accuracy=_delta(
            baseline_metrics.validity_filter_accuracy,
            candidate_metrics.validity_filter_accuracy,
        ),
        refusal_accuracy=_delta(
            baseline_metrics.refusal_accuracy, candidate_metrics.refusal_accuracy
        ),
        checks=checks,
        admitted=all(check.passed for check in checks),
    )


def _minimum_and_regression_check(
    checks: list[LegalEvalAdmissionCheck],
    name: str,
    baseline: float,
    candidate: float,
    minimum: float,
    maximum_regression: float,
) -> None:
    # 同时约束绝对门槛与相对回退，避免低基线或平均分掩盖质量下降。
    passed = candidate >= minimum and candidate >= baseline - maximum_regression
    _check(
        checks,
        name,
        passed,
        f"candidate={candidate:.4f}, minimum={minimum:.4f}, "
        f"baseline={baseline:.4f}, max_regression={maximum_regression:.4f}",
    )


def _check(checks: list[LegalEvalAdmissionCheck], name: str, passed: bool, message: str) -> None:
    checks.append(LegalEvalAdmissionCheck(name=name, passed=passed, message=message))


def _delta(baseline: float, candidate: float) -> LegalEvalMetricDelta:
    return LegalEvalMetricDelta(
        baseline=baseline,
        candidate=candidate,
        delta=candidate - baseline,
    )
