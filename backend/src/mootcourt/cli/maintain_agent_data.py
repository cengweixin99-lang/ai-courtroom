from __future__ import annotations

import argparse
import asyncio

from mootcourt.core.config import get_settings
from mootcourt.db.session import get_session_factory
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.services.agent_data_maintenance import (
    purge_agent_data,
    redact_existing_agent_traces,
)


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    async with get_session_factory()() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        if args.command == "redact":
            redact_result = await redact_existing_agent_traces(
                unit_of_work,
                hmac_key=settings.trace_redaction_hmac_key.get_secret_value(),
                batch_size=args.batch_size,
            )
            print(f"processed={redact_result.processed} failed={redact_result.failed}")
        else:
            purge_result = await purge_agent_data(
                unit_of_work,
                older_than_days=args.trace_older_than_days or settings.agent_trace_retention_days,
                invocation_older_than_days=(
                    args.invocation_older_than_days or settings.agent_invocation_retention_days
                ),
            )
            print(
                f"traces_deleted={purge_result.traces_deleted} "
                f"invocations_deleted={purge_result.invocations_deleted}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain retained Agent diagnostic data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    redact = subparsers.add_parser("redact", help="redact historical full Agent traces")
    redact.add_argument("--batch-size", type=int, default=500)
    purge = subparsers.add_parser("purge", help="delete expired traces and finished invocations")
    purge.add_argument("--trace-older-than-days", type=int)
    purge.add_argument("--invocation-older-than-days", type=int)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
