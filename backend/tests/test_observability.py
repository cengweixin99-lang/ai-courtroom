from typing import Any

from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from mootcourt.core.observability import record_agent_turn, record_provider_retry


def _samples() -> list[Any]:
    return [
        sample
        for family in text_string_to_metric_families(generate_latest().decode("utf-8"))
        for sample in family.samples
    ]


def test_agent_metrics_record_usage_without_high_cardinality_ids() -> None:
    record_agent_turn(
        actor_role="defense",
        action="make_statement",
        status="succeeded",
        error_code=None,
        provider="observability-test-provider",
        model="observability-test-model",
        latency_ms=1_250,
        input_tokens=120,
        output_tokens=30,
        repair_count=1,
        output_normalized=True,
    )
    record_provider_retry(
        provider="observability-test-provider",
        model="observability-test-model",
        reason="http_429",
    )

    matching = [
        sample
        for sample in _samples()
        if sample.labels.get("provider") == "observability-test-provider"
    ]

    assert any(sample.name == "mootcourt_agent_turns_total" for sample in matching)
    assert any(sample.name == "mootcourt_agent_tokens_total" for sample in matching)
    assert any(sample.name == "mootcourt_agent_repairs_total" for sample in matching)
    assert any(sample.name == "mootcourt_agent_provider_retries_total" for sample in matching)
    assert all("session_id" not in sample.labels for sample in matching)
    assert all("request_id" not in sample.labels for sample in matching)
