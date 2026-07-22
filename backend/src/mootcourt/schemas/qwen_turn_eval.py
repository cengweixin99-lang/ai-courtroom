from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from mootcourt.schemas.agents import StrictAgentModel


class QwenTurnEvalCase(StrictAgentModel):
    id: str
    description: str
    action: str
    content: str
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    deterministic_score: int = Field(ge=0, le=100)
    min_scores: dict[str, int]
    max_scores: dict[str, int]
    require_strengths: bool = False
    require_improvements: bool = False
    forbidden_output_tokens: list[str] = Field(default_factory=list)


class QwenTurnEvalDataset(StrictAgentModel):
    dataset: Literal["qwen_turn_quality"]
    version: str
    cases: list[QwenTurnEvalCase] = Field(min_length=5)


class QwenTurnEvalResult(StrictAgentModel):
    id: str
    description: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    passed: bool
    failures: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_cny: float = 0
    latency_ms: float = Field(ge=0)


class QwenTurnEvalReport(StrictAgentModel):
    dataset: str
    dataset_version: str
    provider: str
    model: str
    generated_at: datetime
    case_count: int
    pass_rate: float
    cases: list[QwenTurnEvalResult]
    input_tokens: int
    output_tokens: int
    estimated_cost_cny: float
    passed: bool


def load_qwen_turn_eval_dataset(path: Path) -> QwenTurnEvalDataset:
    try:
        return QwenTurnEvalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise ValueError(f"missing Qwen turn Eval dataset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Qwen turn Eval JSON: {exc}") from exc
