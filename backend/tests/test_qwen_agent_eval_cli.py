from __future__ import annotations

import json

from mootcourt.cli.eval_qwen_agent import _render_report


def test_release_summary_removes_model_content_and_correlatable_identifiers() -> None:
    payload = {
        "dataset": "qwen_agent_quality",
        "dataset_version": "1.0.3",
        "provider": "openai-compatible",
        "model": "qwen3.7-plus",
        "prompt_protocol_version": "agent-grounding-v5-full-output-injection-scan",
        "runtime_config": {"temperature": 0},
        "generated_at": "2026-07-23T00:00:00Z",
        "case_count": 1,
        "passed": True,
        "checks": [{"name": "prompt_injection_leak_rate", "passed": True}],
        "cost": {"input_tokens": 12},
        "token_calibration": {"passed": True},
        "cases": [
            {
                "id": "QWEN-ADV-001",
                "description": "包含案卷语义的场景说明",
                "expected": {"required_cited_evidence_ids": ["E01"]},
                "actual": {"output": {"speech": "不得上传的模型回答"}},
                "passed": True,
                "failures": [],
                "session_id": "session-private-id",
                "trace_id": "trace-private-id",
                "input_tokens": 12,
                "output_tokens": 5,
                "estimated_input_tokens": 14,
                "provider_request_count": 1,
                "input_token_estimation_ratio": 1.17,
                "input_token_underestimated": False,
                "estimated_cost_cny": 0.01,
                "latency_ms": 345,
                "repair_count": 0,
                "output_normalized": False,
            }
        ],
    }

    rendered = _render_report(payload, redact_output=True)
    report = json.loads(rendered)

    assert report["report_schema_version"] == "qwen-agent-release-summary-v1"
    assert report["cases"][0]["id"] == "QWEN-ADV-001"
    assert "actual" not in report["cases"][0]
    assert "expected" not in report["cases"][0]
    assert "session_id" not in report["cases"][0]
    assert "trace_id" not in report["cases"][0]
    assert "不得上传的模型回答" not in rendered
    assert "包含案卷语义的场景说明" not in rendered


def test_local_report_retains_full_payload_for_debugging() -> None:
    payload = {"cases": [{"actual": {"output": {"speech": "本地调试内容"}}}]}

    assert json.loads(_render_report(payload, redact_output=False)) == payload
