from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from mootcourt.domain.courtroom import CourtAction
from mootcourt.schemas.agents import AgentRole, StrictAgentModel
from mootcourt.schemas.runtime import EvidenceChallengeDimension, UserRole


class QwenAgentEvalCase(StrictAgentModel):
    id: str
    description: str
    case_id: str = "CASE-001"
    user_role: UserRole
    advance_count: int = Field(ge=0, le=11)
    actor_role: AgentRole
    action: CourtAction
    participant_id: str | None = None
    target_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    pre_submitted_evidence_ids: list[str] = Field(default_factory=list)
    challenge_dimensions: list[EvidenceChallengeDimension] = Field(default_factory=list)
    instruction: str | None = Field(default=None, max_length=4_000)
    expected_status: Literal["succeeded", "failed"] = "succeeded"
    expected_code: str | None = None
    required_statement_ids: list[str] = Field(default_factory=list)
    expected_consistency_status: str | None = None
    required_cited_evidence_ids: list[str] = Field(default_factory=list)
    min_claim_count: int = Field(default=0, ge=0)
    expected_refusal: bool | None = None
    forbidden_output_tokens: list[str] = Field(default_factory=list)
    max_repair_count: int = Field(default=1, ge=0, le=1)


class QwenAgentEvalDataset(StrictAgentModel):
    dataset: Literal["qwen_agent_quality"]
    version: str
    cases: list[QwenAgentEvalCase] = Field(min_length=10)


class QwenAgentEvalCaseResult(StrictAgentModel):
    id: str
    description: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    passed: bool
    failures: list[str]
    session_id: str | None = None
    trace_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_cny: float = 0
    latency_ms: float = Field(ge=0)
    repair_count: int = 0
    output_normalized: bool = False


class QwenAgentEvalCheck(StrictAgentModel):
    name: str
    target: str
    actual: float
    passed: bool
    blocking: bool


class QwenAgentEvalCost(StrictAgentModel):
    model_call_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost_cny: float
    average_latency_ms: float
    repair_rate: float
    normalization_count: int
    normalization_rate: float


class QwenAgentEvalReport(StrictAgentModel):
    dataset: str
    dataset_version: str
    provider: str
    model: str
    prompt_protocol_version: str
    runtime_config: dict[str, Any]
    generated_at: datetime
    case_count: int
    cases: list[QwenAgentEvalCaseResult]
    checks: list[QwenAgentEvalCheck]
    cost: QwenAgentEvalCost
    passed: bool


def load_qwen_agent_eval_dataset(path: Path) -> QwenAgentEvalDataset:
    try:
        payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing Qwen Agent Eval dataset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Qwen Agent Eval JSON in {path}: {exc}") from exc
    return QwenAgentEvalDataset.model_validate(payload)
