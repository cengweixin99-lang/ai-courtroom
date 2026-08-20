from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from mootcourt.schemas.legal_search import LegalSearchHit, LegalSearchOutcome, StrictLegalModel


class LegalEvalThresholds(StrictLegalModel):
    recall_at_k: float = Field(default=0.9, ge=0, le=1)
    precision_at_k: float = Field(default=0.7, ge=0, le=1)
    validity_filter_accuracy: float = Field(default=1, ge=0, le=1)
    refusal_accuracy: float = Field(default=0.95, ge=0, le=1)


class LegalEvalCase(StrictLegalModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    query: str = Field(min_length=2, max_length=500)
    case_id: str = Field(min_length=1)
    package_version: str | None = None
    expected_outcome: LegalSearchOutcome
    expected_relevant_source_ids: list[str] = Field(default_factory=list)
    forbidden_source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_labels(self) -> LegalEvalCase:
        relevant = set(self.expected_relevant_source_ids)
        forbidden = set(self.forbidden_source_ids)
        if len(relevant) != len(self.expected_relevant_source_ids):
            raise ValueError("expected_relevant_source_ids contains duplicates")
        if len(forbidden) != len(self.forbidden_source_ids):
            raise ValueError("forbidden_source_ids contains duplicates")
        if relevant & forbidden:
            raise ValueError("a source cannot be both relevant and forbidden")
        if self.expected_outcome == LegalSearchOutcome.SUFFICIENT_LEGAL_AUTHORITY and not relevant:
            raise ValueError("sufficient cases require at least one relevant source")
        if self.expected_outcome == LegalSearchOutcome.INSUFFICIENT_LEGAL_AUTHORITY and relevant:
            raise ValueError("insufficient cases cannot declare relevant sources")
        return self


class LegalEvalDataset(StrictLegalModel):
    dataset: str = Field(min_length=1)
    version: str = Field(min_length=1)
    index_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    top_k: int = Field(default=5, ge=1, le=20)
    thresholds: LegalEvalThresholds = Field(default_factory=LegalEvalThresholds)
    cases: list[LegalEvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> LegalEvalDataset:
        case_ids = [item.id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("legal eval case IDs must be unique")
        return self


class LegalEvalFailure(StrEnum):
    OUTCOME_MISMATCH = "OUTCOME_MISMATCH"
    MISSING_RELEVANT_SOURCE = "MISSING_RELEVANT_SOURCE"
    FORBIDDEN_SOURCE_RETRIEVED = "FORBIDDEN_SOURCE_RETRIEVED"


class LegalEvalCaseResult(StrictLegalModel):
    id: str
    category: str
    query: str
    expected_outcome: LegalSearchOutcome
    actual_outcome: LegalSearchOutcome
    expected_relevant_source_ids: list[str]
    forbidden_source_ids: list[str]
    retrieved_source_ids: list[str]
    retrieved_hits: list[LegalSearchHit] = Field(default_factory=list)
    recall_at_k: float | None
    precision_at_k: float | None
    reciprocal_rank: float | None
    latency_ms: float = Field(ge=0)
    failures: list[LegalEvalFailure]
    passed: bool
    trace_id: str | None = None


class LegalEvalMetrics(StrictLegalModel):
    case_count: int = Field(ge=1)
    positive_case_count: int = Field(ge=0)
    refusal_case_count: int = Field(ge=0)
    validity_filter_case_count: int = Field(ge=0)
    recall_at_k: float = Field(ge=0, le=1)
    precision_at_k: float = Field(ge=0, le=1)
    mean_reciprocal_rank: float = Field(ge=0, le=1)
    validity_filter_accuracy: float = Field(ge=0, le=1)
    refusal_accuracy: float = Field(ge=0, le=1)


class LegalEvalReport(StrictLegalModel):
    dataset: str
    dataset_version: str
    index_name: str
    top_k: int
    thresholds: LegalEvalThresholds
    metrics: LegalEvalMetrics
    cases: list[LegalEvalCaseResult]
    passed: bool
    retrieval_mode: str = "bm25"
    embedding_version: str | None = None


class LegalEvalAdmissionPolicy(StrictLegalModel):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    required_baseline_mode: str = "bm25"
    required_candidate_mode: str = "hybrid_rrf"
    minimum_recall_at_k: float = Field(ge=0, le=1)
    maximum_recall_regression: float = Field(ge=0, le=1)
    minimum_precision_at_k: float = Field(ge=0, le=1)
    maximum_precision_regression: float = Field(ge=0, le=1)
    minimum_mean_reciprocal_rank: float = Field(ge=0, le=1)
    maximum_mrr_regression: float = Field(ge=0, le=1)
    minimum_validity_filter_accuracy: float = Field(ge=0, le=1)
    minimum_refusal_accuracy: float = Field(ge=0, le=1)
    require_same_dataset: bool = True
    require_same_case_set: bool = True
    require_candidate_eval_pass: bool = True


class LegalEvalMetricDelta(StrictLegalModel):
    baseline: float
    candidate: float
    delta: float


class LegalEvalAdmissionCheck(StrictLegalModel):
    name: str
    passed: bool
    message: str


class LegalEvalComparisonReport(StrictLegalModel):
    policy_id: str
    policy_version: str
    dataset: str
    baseline_report: str
    candidate_report: str
    candidate_embedding_version: str
    recall_at_k: LegalEvalMetricDelta
    precision_at_k: LegalEvalMetricDelta
    mean_reciprocal_rank: LegalEvalMetricDelta
    validity_filter_accuracy: LegalEvalMetricDelta
    refusal_accuracy: LegalEvalMetricDelta
    checks: list[LegalEvalAdmissionCheck]
    admitted: bool


def load_legal_eval_dataset(dataset_path: Path) -> LegalEvalDataset:
    try:
        raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing legal eval dataset: {dataset_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {dataset_path}: {exc}") from exc
    return LegalEvalDataset.model_validate(raw)


def load_legal_eval_report(report_path: Path) -> LegalEvalReport:
    return LegalEvalReport.model_validate(_read_json(report_path, "legal eval report"))


def load_legal_eval_admission_policy(policy_path: Path) -> LegalEvalAdmissionPolicy:
    return LegalEvalAdmissionPolicy.model_validate(
        _read_json(policy_path, "legal eval admission policy")
    )


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
