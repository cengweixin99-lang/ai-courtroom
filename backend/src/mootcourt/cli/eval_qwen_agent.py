from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from anyio import Path as AsyncPath

from mootcourt.agents.factory import AgentProviderConfigurationError, build_agent_provider
from mootcourt.core.config import get_settings
from mootcourt.core.redis import dispose_redis
from mootcourt.db.session import dispose_engine, get_session_factory
from mootcourt.schemas.qwen_agent_eval import load_qwen_agent_eval_dataset
from mootcourt.services.qwen_agent_eval import evaluate_qwen_agent_suite


async def _run(
    dataset_path: Path,
    output_path: Path | None,
    selected_case_ids: set[str] | None,
    redact_output: bool,
) -> bool:
    settings = get_settings()
    try:
        provider = build_agent_provider(settings, allow_fake=False)
    except AgentProviderConfigurationError as exc:
        raise SystemExit(f"Qwen Agent Eval configuration error: {exc}") from exc
    dataset = load_qwen_agent_eval_dataset(dataset_path)
    try:
        report = await evaluate_qwen_agent_suite(
            get_session_factory(),
            dataset,
            provider,
            settings,
            selected_case_ids,
        )
        rendered = _render_report(report.model_dump(mode="json"), redact_output=redact_output)
        if output_path is not None:
            async_output_path = AsyncPath(output_path)
            await async_output_path.parent.mkdir(parents=True, exist_ok=True)
            await async_output_path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return report.passed
    finally:
        await dispose_redis()
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run real OpenAI-compatible Qwen Agent quality and grounding Eval"
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="run only the selected case; repeat the option to select multiple cases",
    )
    parser.add_argument(
        "--redact-output",
        action="store_true",
        help="write a release-safe summary without model content, session IDs, or Trace IDs",
    )
    args = parser.parse_args()
    passed = asyncio.run(
        _run(
            args.dataset,
            args.output,
            set(args.case_ids) if args.case_ids else None,
            args.redact_output,
        )
    )
    if not passed:
        raise SystemExit(1)


def _render_report(payload: dict[str, Any], *, redact_output: bool) -> str:
    """发布门禁只保存质量信号，避免将案卷内容或可关联标识写入 CI Artifact。"""

    if not redact_output:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    safe_cases = [
        {
            "id": item["id"],
            "passed": item["passed"],
            "failures": item["failures"],
            "input_tokens": item["input_tokens"],
            "output_tokens": item["output_tokens"],
            "estimated_input_tokens": item["estimated_input_tokens"],
            "provider_request_count": item["provider_request_count"],
            "input_token_estimation_ratio": item["input_token_estimation_ratio"],
            "input_token_underestimated": item["input_token_underestimated"],
            "estimated_cost_cny": item["estimated_cost_cny"],
            "latency_ms": item["latency_ms"],
            "repair_count": item["repair_count"],
            "output_normalized": item["output_normalized"],
            "actual_status": item["actual"]["status"],
            "actual_code": item["actual"]["code"],
            "provider_http_status": item["provider_http_status"],
        }
        for item in payload["cases"]
    ]
    safe_payload = {
        "report_schema_version": "qwen-agent-release-summary-v1",
        "dataset": payload["dataset"],
        "dataset_version": payload["dataset_version"],
        "provider": payload["provider"],
        "model": payload["model"],
        "prompt_protocol_version": payload["prompt_protocol_version"],
        "runtime_config": payload["runtime_config"],
        "generated_at": payload["generated_at"],
        "case_count": payload["case_count"],
        "passed": payload["passed"],
        "checks": payload["checks"],
        "cost": payload["cost"],
        "token_calibration": payload["token_calibration"],
        "cases": safe_cases,
    }
    return json.dumps(safe_payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
