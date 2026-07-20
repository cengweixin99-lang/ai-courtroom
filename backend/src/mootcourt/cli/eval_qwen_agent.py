from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from anyio import Path as AsyncPath

from mootcourt.agents.factory import AgentProviderConfigurationError, build_agent_provider
from mootcourt.core.config import get_settings
from mootcourt.db.session import dispose_engine, get_session_factory
from mootcourt.schemas.qwen_agent_eval import load_qwen_agent_eval_dataset
from mootcourt.services.qwen_agent_eval import evaluate_qwen_agent_suite


async def _run(
    dataset_path: Path,
    output_path: Path | None,
    selected_case_ids: set[str] | None,
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
        rendered = report.model_dump_json(indent=2)
        if output_path is not None:
            async_output_path = AsyncPath(output_path)
            await async_output_path.parent.mkdir(parents=True, exist_ok=True)
            await async_output_path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return report.passed
    finally:
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
    args = parser.parse_args()
    passed = asyncio.run(
        _run(
            args.dataset,
            args.output,
            set(args.case_ids) if args.case_ids else None,
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
