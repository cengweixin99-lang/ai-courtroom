from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter

from mootcourt.agents.providers import StructuredAgentProvider, StructuredProviderRequest
from mootcourt.schemas.eval.qwen_turn_eval import (
    QwenTurnEvalCase,
    QwenTurnEvalDataset,
    QwenTurnEvalReport,
    QwenTurnEvalResult,
)
from mootcourt.schemas.reviews import TurnQualityEvaluationBatch


async def evaluate_qwen_turn_suite(
    dataset: QwenTurnEvalDataset, provider: StructuredAgentProvider
) -> QwenTurnEvalReport:
    results = [await _evaluate(item, provider) for item in dataset.cases]
    pass_rate = sum(item.passed for item in results) / len(results)
    return QwenTurnEvalReport(
        dataset=dataset.dataset,
        dataset_version=dataset.version,
        provider=provider.provider_name,
        model=provider.model_name,
        generated_at=datetime.now(UTC),
        case_count=len(results),
        pass_rate=pass_rate,
        cases=results,
        input_tokens=sum(item.input_tokens for item in results),
        output_tokens=sum(item.output_tokens for item in results),
        estimated_cost_cny=sum(item.estimated_cost_cny for item in results),
        passed=pass_rate >= 0.8
        and all("ANCHOR_SCOPE_VIOLATION" not in item.failures for item in results),
    )


async def _evaluate(
    item: QwenTurnEvalCase, provider: StructuredAgentProvider
) -> QwenTurnEvalResult:
    started = perf_counter()
    schema = TurnQualityEvaluationBatch.model_json_schema()
    payload = {
        "classification": "UNTRUSTED_COURTROOM_TRANSCRIPT",
        "allowed_event_sequences": [1],
        "turns": [
            {
                "diagnostic": {
                    "event_sequence_number": 1,
                    "actor_role": "defense",
                    "phase": "COURT_DEBATE_DEFENSE",
                    "action": item.action,
                    "score": item.deterministic_score,
                    "evidence_ids": item.evidence_ids,
                    "fact_ids": item.fact_ids,
                },
                "content": item.content,
            }
        ],
    }
    system = (
        "你是刑事模拟庭审教学点评器。只评价表达组织、回应质量和攻防策略；不得补充事实或"
        "法律结论。event_sequence_number必须为1，证据和事实ID只能从允许列表选择。若"
        "evidence_ids或fact_ids任一为空，rewritten_example必须为null。只输出JSON。"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    result = await provider.generate_structured(
        StructuredProviderRequest(
            messages=(
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ),
            schema_name="mootcourt_qwen_turn_eval",
            response_schema=schema,
            fallback_output={"evaluations": []},
        )
    )
    failures: list[str] = []
    actual: dict[str, object] = {"output": result.output}
    try:
        evaluation = TurnQualityEvaluationBatch.model_validate(result.output).evaluations[0]
        if not item.evidence_ids or not item.fact_ids:
            evaluation = evaluation.model_copy(update={"rewritten_example": None})
        scores = {
            "organization_score": evaluation.organization_score,
            "responsiveness_score": evaluation.responsiveness_score,
            "advocacy_score": evaluation.advocacy_score,
        }
        actual.update(scores)
        if any(scores[key] < value for key, value in item.min_scores.items()):
            failures.append("SCORE_BELOW_HUMAN_RANGE")
        if any(scores[key] > value for key, value in item.max_scores.items()):
            failures.append("SCORE_ABOVE_HUMAN_RANGE")
        if item.require_strengths and not evaluation.strengths:
            failures.append("STRENGTH_MISSING")
        if item.require_improvements and not evaluation.improvements:
            failures.append("IMPROVEMENT_MISSING")
        if not set(evaluation.evidence_ids).issubset(item.evidence_ids) or not set(
            evaluation.fact_ids
        ).issubset(item.fact_ids):
            failures.append("ANCHOR_SCOPE_VIOLATION")
        if evaluation.rewritten_example and any(
            token in evaluation.rewritten_example for token in item.forbidden_output_tokens
        ):
            failures.append("FORBIDDEN_OUTPUT_LEAK")
        actual["rewrite_enabled"] = evaluation.rewritten_example is not None
    except Exception as exc:
        failures.append(f"INVALID_OUTPUT_{type(exc).__name__}")
    return QwenTurnEvalResult(
        id=item.id,
        description=item.description,
        expected={"min_scores": item.min_scores, "max_scores": item.max_scores},
        actual=actual,
        passed=not failures,
        failures=failures,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_cny=result.estimated_cost_cny,
        latency_ms=max(0, (perf_counter() - started) * 1000),
    )
