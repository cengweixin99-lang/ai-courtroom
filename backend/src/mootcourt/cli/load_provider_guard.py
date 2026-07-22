from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from anyio import Path as AsyncPath

from mootcourt.core.config import get_settings
from mootcourt.services.provider_guard_load import run_provider_guard_load


async def _run(args: argparse.Namespace) -> bool:
    redis_url = args.redis_url or os.getenv("TEST_REDIS_URL") or get_settings().redis_url
    if not redis_url:
        raise SystemExit("Redis URL is required via --redis-url, TEST_REDIS_URL, or REDIS_URL")
    report = await run_provider_guard_load(
        redis_url,
        replica_count=args.replicas,
        request_count=args.requests,
        max_concurrency=args.max_concurrency,
        requests_per_second=args.requests_per_second,
        queue_timeout_seconds=args.queue_timeout_seconds,
        hold_ms=args.hold_ms,
        key_prefix=args.key_prefix,
    )
    rendered = report.model_dump_json(indent=2)
    if args.output is not None:
        output = AsyncPath(args.output)
        await output.parent.mkdir(parents=True, exist_ok=True)
        await output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return report.passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stress the Redis-backed model Provider concurrency and circuit guard"
    )
    parser.add_argument("--redis-url", help="Redis endpoint; never written to the report")
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--requests-per-second", type=float, default=0)
    parser.add_argument("--queue-timeout-seconds", type=float, default=0.05)
    parser.add_argument("--hold-ms", type=int, default=100)
    parser.add_argument("--key-prefix", default="mootcourt:load")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not asyncio.run(_run(args)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
