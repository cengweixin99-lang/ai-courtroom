from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from anyio import Path as AsyncPath
from sqlalchemy.ext.asyncio import create_async_engine

from mootcourt.core.config import get_settings
from mootcourt.repositories.deployment import DeploymentRepository
from mootcourt.services.delivery_acceptance import (
    EXPECTED_DATABASE_REVISION,
    delivery_report_markdown,
    run_delivery_acceptance,
)


def _default_output_path(*, full: bool, generated_at: datetime | None = None) -> Path:
    """Keep the rolling smoke report stable while preserving each paid full-run report."""

    output_directory = Path("../evals/delivery/results")
    if not full:
        return output_directory / "smoke.json"
    timestamp = (generated_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return output_directory / f"full_{timestamp}.json"


async def _run(args: argparse.Namespace) -> bool:
    settings = get_settings()
    database_url = args.database_url or settings.database_url
    engine = create_async_engine(database_url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout, connect=10)) as client:
            report = await run_delivery_acceptance(
                client=client,
                deployment_repository=DeploymentRepository(engine),
                api_base_url=args.api_base_url.rstrip("/"),
                web_url=args.web_url.rstrip("/"),
                elasticsearch_url=args.elasticsearch_url.rstrip("/"),
                case_id=args.case_id,
                full=args.full,
            )
    finally:
        await engine.dispose()
    output = AsyncPath(args.output)
    await output.parent.mkdir(parents=True, exist_ok=True)
    await output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_output = AsyncPath(args.markdown_output or args.output.with_suffix(".md"))
    await markdown_output.parent.mkdir(parents=True, exist_ok=True)
    await markdown_output.write_text(delivery_report_markdown(report), encoding="utf-8")
    print(report.model_dump_json(indent=2))
    return report.passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Docker delivery acceptance through public HTTP APIs"
    )
    parser.add_argument("--api-base-url", default="http://localhost:8000/api/v1")
    parser.add_argument("--web-url", default="http://localhost:5173")
    parser.add_argument("--elasticsearch-url", default="http://localhost:9200")
    parser.add_argument("--database-url")
    parser.add_argument("--case-id", default="CASE-001")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--full", action="store_true", help="invoke real LLM and finish a trial")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    if args.output is None:
        args.output = _default_output_path(full=args.full)

    # 迁移期望值与 Alembic 唯一 head 必须同步，避免脚本自身成为过期真相源。
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if head != EXPECTED_DATABASE_REVISION:
        raise SystemExit(
            f"acceptance expected revision {EXPECTED_DATABASE_REVISION}, Alembic head is {head}"
        )
    if not asyncio.run(_run(args)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
