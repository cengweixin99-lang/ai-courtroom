from __future__ import annotations

import asyncio
import math
from collections import Counter
from collections.abc import Sequence
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from redis.asyncio import Redis

from mootcourt.agents.provider_resilience import (
    ProviderResilienceError,
    RedisProviderResilience,
)
from mootcourt.schemas.provider_guard_load import ProviderGuardLoadReport


class ProviderGuard(Protocol):
    async def acquire(self) -> None: ...

    async def before_request(self) -> None: ...

    async def record_failure(self, *, transient: bool) -> None: ...

    async def release_async(self) -> None: ...


async def run_provider_guard_load(
    redis_url: str,
    *,
    replica_count: int = 2,
    request_count: int = 32,
    max_concurrency: int = 4,
    requests_per_second: float = 0,
    queue_timeout_seconds: float = 0.05,
    hold_ms: int = 100,
    key_prefix: str = "mootcourt:load",
) -> ProviderGuardLoadReport:
    _validate_load_parameters(
        redis_url=redis_url,
        replica_count=replica_count,
        request_count=request_count,
        max_concurrency=max_concurrency,
        requests_per_second=requests_per_second,
        queue_timeout_seconds=queue_timeout_seconds,
        hold_ms=hold_ms,
    )
    namespace = f"{key_prefix}:{uuid4()}"
    clients = [Redis.from_url(redis_url, decode_responses=False) for _ in range(replica_count)]
    guards: list[ProviderGuard] = [
        RedisProviderResilience(
            client,
            namespace=namespace,
            max_concurrency=max_concurrency,
            requests_per_second=requests_per_second,
            queue_timeout_seconds=queue_timeout_seconds,
            circuit_failure_threshold=1,
            circuit_recovery_seconds=30,
            lease_seconds=max(10, hold_ms / 1_000 * 4),
        )
        for client in clients
    ]
    try:
        if not await clients[0].ping():
            raise RuntimeError("Redis did not acknowledge PING")
        return await _execute_provider_guard_load(
            guards,
            namespace=namespace,
            request_count=request_count,
            max_concurrency=max_concurrency,
            hold_ms=hold_ms,
        )
    finally:
        try:
            await clients[0].delete(*(f"{namespace}:{suffix}" for suffix in _STATE_SUFFIXES))
        finally:
            await asyncio.gather(*(client.aclose() for client in clients))


async def _execute_provider_guard_load(
    guards: Sequence[ProviderGuard],
    *,
    namespace: str,
    request_count: int,
    max_concurrency: int,
    hold_ms: int,
) -> ProviderGuardLoadReport:
    start_gate = asyncio.Event()
    state_lock = asyncio.Lock()
    rejection_codes: Counter[str] = Counter()
    acquire_latencies: list[float] = []
    accepted_count = 0
    active_count = 0
    observed_max = 0
    unexpected_error_count = 0
    started = perf_counter()

    async def invoke(index: int) -> None:
        nonlocal accepted_count, active_count, observed_max, unexpected_error_count
        guard = guards[index % len(guards)]
        await start_gate.wait()
        acquired = False
        acquire_started = perf_counter()
        try:
            await guard.acquire()
            acquired = True
            await guard.before_request()
            acquire_latency = (perf_counter() - acquire_started) * 1_000
            async with state_lock:
                accepted_count += 1
                active_count += 1
                observed_max = max(observed_max, active_count)
                acquire_latencies.append(acquire_latency)
            await asyncio.sleep(hold_ms / 1_000)
        except ProviderResilienceError as exc:
            async with state_lock:
                rejection_codes[exc.code] += 1
        except Exception:
            async with state_lock:
                unexpected_error_count += 1
        finally:
            if acquired:
                async with state_lock:
                    active_count = max(0, active_count - 1)
                await guard.release_async()

    tasks = [asyncio.create_task(invoke(index)) for index in range(request_count)]
    start_gate.set()
    await asyncio.gather(*tasks)

    # 并发压测完成后由一个实例打开熔断，另一个实例必须立即观察到共享状态。
    circuit_shared = False
    circuit_guard = guards[0]
    peer_guard = guards[1]
    circuit_acquired = False
    try:
        await circuit_guard.acquire()
        circuit_acquired = True
        await circuit_guard.record_failure(transient=True)
    finally:
        if circuit_acquired:
            await circuit_guard.release_async()
    try:
        await peer_guard.acquire()
    except ProviderResilienceError as exc:
        circuit_shared = exc.code == "agent_provider_circuit_open"
    else:
        await peer_guard.release_async()

    elapsed_ms = (perf_counter() - started) * 1_000
    overload_expected = request_count > max_concurrency
    overloaded = rejection_codes["agent_provider_overloaded"] > 0
    passed = (
        accepted_count > 0
        and observed_max <= max_concurrency
        and (not overload_expected or overloaded)
        and unexpected_error_count == 0
        and circuit_shared
    )
    return ProviderGuardLoadReport(
        namespace=namespace,
        replica_count=len(guards),
        request_count=request_count,
        configured_max_concurrency=max_concurrency,
        accepted_count=accepted_count,
        rejection_codes=dict(sorted(rejection_codes.items())),
        unexpected_error_count=unexpected_error_count,
        observed_max_concurrency=observed_max,
        acquire_latency_p95_ms=_percentile(acquire_latencies, 0.95),
        acquire_latency_p99_ms=_percentile(acquire_latencies, 0.99),
        elapsed_ms=elapsed_ms,
        circuit_shared=circuit_shared,
        passed=passed,
    )


def _validate_load_parameters(
    *,
    redis_url: str,
    replica_count: int,
    request_count: int,
    max_concurrency: int,
    requests_per_second: float,
    queue_timeout_seconds: float,
    hold_ms: int,
) -> None:
    if not redis_url.startswith(("redis://", "rediss://")):
        raise ValueError("redis_url must use redis:// or rediss://")
    if replica_count < 2:
        raise ValueError("replica_count must be at least 2")
    if request_count < 1 or max_concurrency < 1:
        raise ValueError("request_count and max_concurrency must be positive")
    if requests_per_second < 0:
        raise ValueError("requests_per_second must not be negative")
    if queue_timeout_seconds <= 0 or hold_ms < 1:
        raise ValueError("queue_timeout_seconds and hold_ms must be positive")


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


_STATE_SUFFIXES = ("active", "rate", "failures", "open-until", "probe")
