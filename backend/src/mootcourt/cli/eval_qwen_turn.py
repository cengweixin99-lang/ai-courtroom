from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import cast

from anyio import Path as AsyncPath

from mootcourt.agents.factory import build_agent_provider
from mootcourt.agents.providers import StructuredAgentProvider
from mootcourt.core.config import get_settings
from mootcourt.core.redis import dispose_redis
from mootcourt.schemas.eval.qwen_turn_eval import load_qwen_turn_eval_dataset
from mootcourt.services.qwen_turn_eval import evaluate_qwen_turn_suite


async def _run(dataset: Path, output: Path) -> bool:
    try:
        provider = cast(
            StructuredAgentProvider,
            build_agent_provider(get_settings(), allow_fake=False),
        )
        report = await evaluate_qwen_turn_suite(load_qwen_turn_eval_dataset(dataset), provider)
        async_output = AsyncPath(output)
        await async_output.parent.mkdir(parents=True, exist_ok=True)
        await async_output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(report.model_dump_json(indent=2))
        return report.passed
    finally:
        await dispose_redis()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real Qwen courtroom turn quality Eval")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not asyncio.run(_run(args.dataset, args.output)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
