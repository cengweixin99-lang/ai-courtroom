from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mootcourt.agents.provider_resilience import ProviderResilienceError
from mootcourt.services.provider_guard_load import (
    _execute_provider_guard_load,
    _percentile,
)


@dataclass
class SharedGuardState:
    max_concurrency: int
    active: int = 0
    circuit_open: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class FakeDistributedGuard:
    def __init__(self, state: SharedGuardState) -> None:
        self._state = state
        self._acquired = False

    async def acquire(self) -> None:
        async with self._state.lock:
            if self._state.circuit_open:
                raise ProviderResilienceError("agent_provider_circuit_open", "open")
            if self._state.active >= self._state.max_concurrency:
                raise ProviderResilienceError("agent_provider_overloaded", "full")
            self._state.active += 1
            self._acquired = True

    async def before_request(self) -> None:
        return None

    async def record_failure(self, *, transient: bool) -> None:
        if transient:
            self._state.circuit_open = True

    async def release_async(self) -> None:
        async with self._state.lock:
            if self._acquired:
                self._state.active -= 1
                self._acquired = False


async def test_provider_guard_load_enforces_shared_concurrency_and_circuit() -> None:
    state = SharedGuardState(max_concurrency=2)
    guards = [FakeDistributedGuard(state) for _ in range(3)]

    report = await _execute_provider_guard_load(
        guards,
        namespace="test:guard-load",
        request_count=20,
        max_concurrency=2,
        hold_ms=10,
    )

    assert report.passed
    assert report.replica_count == 3
    assert report.accepted_count == 2
    assert report.rejection_codes == {"agent_provider_overloaded": 18}
    assert report.observed_max_concurrency == 2
    assert report.circuit_shared
    assert report.unexpected_error_count == 0


def test_percentile_uses_nearest_rank_without_optional_dependencies() -> None:
    values = [float(item) for item in range(1, 101)]

    assert _percentile([], 0.95) == 0
    assert _percentile(values, 0.95) == 95
    assert _percentile(values, 0.99) == 99
