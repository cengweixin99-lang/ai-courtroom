from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mootcourt.schemas.agents import AgentRole
from mootcourt.schemas.eval.legal_eval import LegalEvalReport
from mootcourt.schemas.runtime import SessionActionRequest, UserRole


class StrictEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class M5Subset(StrEnum):
    PROCEDURE_PERMISSIONS = "procedure_permissions"
    PARTICIPANT_BOUNDARIES = "participant_boundaries"
    LEGAL_RAG = "legal_rag"
    END_TO_END = "end_to_end"


class ProcedureEvalCase(StrictEvalModel):
    id: str
    description: str
    metric: str
    case_id: str = "CASE-001"
    user_role: UserRole
    advance_count: int = Field(ge=0, le=11)
    pre_submitted_evidence_ids: list[str] = Field(default_factory=list)
    setup_actions: list[SessionActionRequest] = Field(default_factory=list)
    action: SessionActionRequest
    expected_code: str
    expected_allowed: bool


class ProcedureEvalDataset(StrictEvalModel):
    dataset: Literal[M5Subset.PROCEDURE_PERMISSIONS]
    version: str
    cases: list[ProcedureEvalCase] = Field(min_length=15)


class ParticipantEvalCase(StrictEvalModel):
    id: str
    description: str
    metric: str
    case_id: str = "CASE-001"
    user_role: UserRole
    advance_count: int = Field(ge=0, le=11)
    actor_role: AgentRole
    participant_id: str | None = None
    action: str
    target_id: str | None = None
    provider_output: dict[str, Any] | None = None
    forbidden_context_tokens: list[str] = Field(default_factory=list)
    expected_status: Literal["succeeded", "failed", "rejected"]
    expected_code: str | None = None
    expected_statement_ids: list[str] = Field(default_factory=list)
    expected_consistency_status: str | None = None


class ParticipantEvalDataset(StrictEvalModel):
    dataset: Literal[M5Subset.PARTICIPANT_BOUNDARIES]
    version: str
    cases: list[ParticipantEvalCase] = Field(min_length=10)


class EndToEndEvalCase(StrictEvalModel):
    id: str
    description: str
    case_id: str = "CASE-001"
    user_role: UserRole
    evidence_ids: list[str] = Field(min_length=1)


class EndToEndEvalDataset(StrictEvalModel):
    dataset: Literal[M5Subset.END_TO_END]
    version: str
    cases: list[EndToEndEvalCase] = Field(min_length=5)


class M5EvalManifest(StrictEvalModel):
    suite: str
    version: str
    procedure_permissions: str
    participant_boundaries: str
    legal_rag: str
    end_to_end: str


class EvalCaseResult(StrictEvalModel):
    id: str
    subset: M5Subset
    metric: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    passed: bool
    failures: list[str]
    session_id: str | None = None
    trace_ids: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_cny: float = 0
    latency_ms: float = Field(ge=0)
    repair_count: int = 0


class EvalMetricCheck(StrictEvalModel):
    name: str
    target: str
    actual: float
    passed: bool
    blocking: bool


class M5CostMetrics(StrictEvalModel):
    agent_call_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost_cny: float
    average_agent_latency_ms: float
    repair_rate: float


class M5EvalReport(StrictEvalModel):
    suite: str
    suite_version: str
    generated_at: datetime
    total_case_count: int
    subset_counts: dict[M5Subset, int]
    cases: list[EvalCaseResult]
    legal_report: LegalEvalReport
    checks: list[EvalMetricCheck]
    cost: M5CostMetrics
    passed: bool


class LoadedM5Datasets(StrictEvalModel):
    manifest: M5EvalManifest
    procedure: ProcedureEvalDataset
    participants: ParticipantEvalDataset
    end_to_end: EndToEndEvalDataset
    legal_path: Path

    @model_validator(mode="after")
    def validate_total_count(self) -> LoadedM5Datasets:
        total = (
            len(self.procedure.cases) + len(self.participants.cases) + len(self.end_to_end.cases)
        )
        if total != 30:
            raise ValueError("non-legal M5 Eval subsets must contain exactly 30 cases")
        return self


def load_m5_eval_datasets(manifest_path: Path) -> LoadedM5Datasets:
    manifest_path = manifest_path.resolve()
    manifest = M5EvalManifest.model_validate(_read_json(manifest_path))
    base = manifest_path.parent
    return LoadedM5Datasets(
        manifest=manifest,
        procedure=ProcedureEvalDataset.model_validate(
            _read_json((base / manifest.procedure_permissions).resolve())
        ),
        participants=ParticipantEvalDataset.model_validate(
            _read_json((base / manifest.participant_boundaries).resolve())
        ),
        end_to_end=EndToEndEvalDataset.model_validate(
            _read_json((base / manifest.end_to_end).resolve())
        ),
        legal_path=(base / manifest.legal_rag).resolve(),
    )


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing Eval dataset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Eval JSON in {path}: {exc}") from exc
