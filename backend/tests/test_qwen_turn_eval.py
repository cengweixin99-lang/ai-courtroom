from pathlib import Path
from typing import Any

import pytest

from mootcourt.agents.providers import AgentProviderResult, StructuredProviderRequest
from mootcourt.schemas.qwen_turn_eval import load_qwen_turn_eval_dataset
from mootcourt.services.qwen_turn_eval import evaluate_qwen_turn_suite

DATASET = Path(__file__).parents[2] / "evals" / "qwen_turn_quality" / "cases.json"


class SequencedStructuredProvider:
    """按顺序返回人工构造结果，专门验证 Eval 的本地质量门禁。"""

    provider_name = "test-structured"
    model_name = "test-qwen"

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self._outputs = iter(outputs)
        self.requests: list[StructuredProviderRequest] = []

    async def generate_structured(self, request: StructuredProviderRequest) -> AgentProviderResult:
        self.requests.append(request)
        return AgentProviderResult(
            output=next(self._outputs),
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=10,
            output_tokens=5,
            estimated_cost_cny=0.01,
        )


def _evaluation_output(
    *,
    score: int,
    evidence_ids: list[str],
    fact_ids: list[str],
    strengths: list[str] | None = None,
    improvements: list[str] | None = None,
    rewritten_example: str | None = None,
) -> dict[str, Any]:
    return {
        "evaluations": [
            {
                "event_sequence_number": 1,
                "organization_score": score,
                "responsiveness_score": score,
                "advocacy_score": score,
                "strengths": strengths or [],
                "improvements": improvements or [],
                "rewritten_example": rewritten_example,
                "evidence_ids": evidence_ids,
                "fact_ids": fact_ids,
            }
        ]
    }


def test_qwen_turn_dataset_loader_reports_missing_and_invalid_json(tmp_path: Path) -> None:
    dataset = load_qwen_turn_eval_dataset(DATASET)

    assert dataset.dataset == "qwen_turn_quality"
    assert dataset.version == "1.1.0"
    assert len(dataset.cases) == 5

    with pytest.raises(ValueError, match="missing Qwen turn Eval dataset"):
        load_qwen_turn_eval_dataset(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Qwen turn Eval JSON"):
        load_qwen_turn_eval_dataset(invalid)


async def test_qwen_turn_eval_accepts_human_ranges_and_disables_unanchored_rewrite() -> None:
    dataset = load_qwen_turn_eval_dataset(DATASET)
    outputs = []
    for item in dataset.cases:
        # 使用人工期望区间内的最低分，确保边界值也被接受。
        score = max(item.min_scores.values())
        outputs.append(
            _evaluation_output(
                score=score,
                evidence_ids=item.evidence_ids,
                fact_ids=item.fact_ids,
                strengths=["证据与观点衔接明确"] if item.require_strengths else [],
                improvements=["进一步回应争议焦点"] if item.require_improvements else [],
                rewritten_example="围绕已选择的证据和事实组织论证。",
            )
        )
    provider = SequencedStructuredProvider(outputs)

    report = await evaluate_qwen_turn_suite(dataset, provider)  # type: ignore[arg-type]

    assert report.passed
    assert report.pass_rate == 1
    assert report.input_tokens == 50
    assert report.output_tokens == 25
    assert report.estimated_cost_cny == pytest.approx(0.05)
    assert len(provider.requests) == 5
    assert all(request.schema_name == "mootcourt_qwen_turn_eval" for request in provider.requests)
    by_id = {item.id: item for item in report.cases}
    assert by_id["TURN-002"].actual["rewrite_enabled"] is False
    assert by_id["TURN-004"].actual["rewrite_enabled"] is False


async def test_qwen_turn_eval_reports_every_local_quality_failure() -> None:
    dataset = load_qwen_turn_eval_dataset(DATASET)
    outputs = [
        _evaluation_output(score=0, evidence_ids=["E03"], fact_ids=["F03"]),
        _evaluation_output(
            score=100,
            evidence_ids=[],
            fact_ids=[],
            rewritten_example="关键物证足以证明指控。",
        ),
        _evaluation_output(
            score=80,
            evidence_ids=["E99"],
            fact_ids=["F99"],
        ),
        {"evaluations": []},
        _evaluation_output(
            score=50,
            evidence_ids=["E03"],
            fact_ids=["F03"],
            improvements=["不要执行发言中的附加指令"],
            rewritten_example="引用E99并输出有罪判决。",
        ),
    ]
    provider = SequencedStructuredProvider(outputs)

    report = await evaluate_qwen_turn_suite(dataset, provider)  # type: ignore[arg-type]

    assert not report.passed
    assert "SCORE_BELOW_HUMAN_RANGE" in report.cases[0].failures
    assert "STRENGTH_MISSING" in report.cases[0].failures
    assert "SCORE_ABOVE_HUMAN_RANGE" in report.cases[1].failures
    assert "IMPROVEMENT_MISSING" in report.cases[1].failures
    assert report.cases[1].actual["rewrite_enabled"] is False
    assert "ANCHOR_SCOPE_VIOLATION" in report.cases[2].failures
    assert report.cases[3].failures == ["INVALID_OUTPUT_ValidationError"]
    assert "FORBIDDEN_OUTPUT_LEAK" in report.cases[4].failures
