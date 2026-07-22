from __future__ import annotations

import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from mootcourt.agents.provider_resilience import (
    ProviderResilienceError,
    RedisProviderResilience,
)
from mootcourt.services.provider_guard_load import run_provider_guard_load

REDIS_URL = os.getenv("TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="TEST_REDIS_URL is not configured")


async def test_two_instances_share_concurrency_and_circuit_state() -> None:
    client = Redis.from_url(REDIS_URL, decode_responses=False)
    namespace = f"mootcourt:test:{uuid4()}"

    def resilience() -> RedisProviderResilience:
        return RedisProviderResilience(
            client,
            namespace=namespace,
            max_concurrency=1,
            requests_per_second=0,
            queue_timeout_seconds=0.1,
            circuit_failure_threshold=1,
            circuit_recovery_seconds=10,
            lease_seconds=10,
        )

    first = resilience()
    second = resilience()
    try:
        assert await client.ping() is True
        await first.acquire()
        with pytest.raises(ProviderResilienceError) as overloaded:
            await second.acquire()
        assert overloaded.value.code == "agent_provider_overloaded"
        await first.record_failure(transient=True)
        await first.release_async()

        with pytest.raises(ProviderResilienceError) as opened:
            await second.acquire()
        assert opened.value.code == "agent_provider_circuit_open"
    finally:
        await client.delete(
            *(
                f"{namespace}:{suffix}"
                for suffix in ("active", "rate", "failures", "open-until", "probe")
            )
        )
        await client.aclose()


async def test_real_redis_provider_guard_load_report_passes() -> None:
    report = await run_provider_guard_load(
        REDIS_URL,
        replica_count=3,
        request_count=24,
        max_concurrency=3,
        hold_ms=25,
        key_prefix="mootcourt:test-load",
    )

    assert report.passed
    assert report.accepted_count == 3
    assert report.rejection_codes == {"agent_provider_overloaded": 21}
    assert report.observed_max_concurrency == 3
    assert report.circuit_shared
