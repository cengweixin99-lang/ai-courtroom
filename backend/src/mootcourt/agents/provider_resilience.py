from __future__ import annotations

import asyncio
import contextvars
import hashlib
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4


class ProviderResilienceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProviderRuntimeGate:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._accepting = True
        self._active = 0

    async def enter(self) -> None:
        async with self._condition:
            if not self._accepting:
                raise ProviderResilienceError(
                    "agent_provider_draining", "API instance is draining model requests"
                )
            self._active += 1

    async def leave(self) -> None:
        async with self._condition:
            self._active = max(0, self._active - 1)
            if self._active == 0:
                self._condition.notify_all()

    async def drain(self, timeout_seconds: float) -> bool:
        async with self._condition:
            self._accepting = False
            try:
                async with asyncio.timeout(timeout_seconds):
                    while self._active:
                        await self._condition.wait()
            except TimeoutError:
                return False
            return True

    async def resume(self) -> None:
        async with self._condition:
            self._accepting = True


_RUNTIME_GATE = ProviderRuntimeGate()


async def enter_provider_call() -> None:
    await _RUNTIME_GATE.enter()


async def leave_provider_call() -> None:
    await _RUNTIME_GATE.leave()


async def drain_provider_calls(timeout_seconds: float) -> bool:
    return await _RUNTIME_GATE.drain(timeout_seconds)


async def resume_provider_calls() -> None:
    await _RUNTIME_GATE.resume()


class ProviderResilience:
    """进程内共享的 Provider 并发闸门、速率限制器和熔断器。"""

    def __init__(
        self,
        *,
        max_concurrency: int,
        requests_per_second: float,
        queue_timeout_seconds: float,
        circuit_failure_threshold: int,
        circuit_recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._requests_per_second = requests_per_second
        self._queue_timeout_seconds = queue_timeout_seconds
        self._failure_threshold = circuit_failure_threshold
        self._recovery_seconds = circuit_recovery_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._next_request_at = 0.0

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._queue_timeout_seconds
            )
        except TimeoutError as exc:
            raise ProviderResilienceError(
                "agent_provider_overloaded", "model request concurrency queue is full"
            ) from exc

        try:
            await self._check_circuit()
        except Exception:
            self._semaphore.release()
            raise

    async def before_request(self) -> None:
        """每一次真实上游 HTTP 尝试都必须取得速率配额，包括重试和重生成。"""
        await self._apply_rate_limit()

    def release(self) -> None:
        self._semaphore.release()

    async def release_async(self) -> None:
        self.release()

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    async def record_failure(self, *, transient: bool) -> None:
        async with self._lock:
            if not transient:
                self._failures = 0
                self._opened_at = None
                self._half_open_in_flight = False
                return
            self._failures += 1
            if self._half_open_in_flight or self._failures >= self._failure_threshold:
                self._opened_at = self._clock()
                self._half_open_in_flight = False

    async def record_local_rejection(self) -> None:
        async with self._lock:
            # 半开探测尚未到达上游时维持 open，等待下一次恢复探测。
            self._half_open_in_flight = False

    async def _check_circuit(self) -> None:
        async with self._lock:
            if self._opened_at is None:
                return
            if self._clock() - self._opened_at < self._recovery_seconds:
                raise ProviderResilienceError(
                    "agent_provider_circuit_open",
                    "model provider circuit is open after repeated transient failures",
                )
            if self._half_open_in_flight:
                raise ProviderResilienceError(
                    "agent_provider_circuit_open",
                    "model provider circuit is waiting for a half-open probe",
                )
            self._half_open_in_flight = True

    async def _apply_rate_limit(self) -> None:
        if self._requests_per_second <= 0:
            return
        async with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_request_at - now)
            if delay > self._queue_timeout_seconds:
                raise ProviderResilienceError(
                    "agent_provider_rate_limited",
                    "model request local rate limit queue is full",
                )
            self._next_request_at = max(now, self._next_request_at) + (
                1 / self._requests_per_second
            )
        if delay > 0:
            await asyncio.sleep(delay)


_REGISTRY: dict[str, ProviderResilience] = {}


def shared_provider_resilience(
    key: str,
    *,
    redis_client: Any = None,
    redis_key_prefix: str = "mootcourt:provider",
    distributed_lease_seconds: float = 900,
    max_concurrency: int,
    requests_per_second: float,
    queue_timeout_seconds: float,
    circuit_failure_threshold: int,
    circuit_recovery_seconds: float,
) -> ProviderResilience:
    resilience = _REGISTRY.get(key)
    if resilience is None:
        distributed_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        resilience = (
            RedisProviderResilience(
                redis_client,
                namespace=f"{redis_key_prefix}:{distributed_key}",
                max_concurrency=max_concurrency,
                requests_per_second=requests_per_second,
                queue_timeout_seconds=queue_timeout_seconds,
                circuit_failure_threshold=circuit_failure_threshold,
                circuit_recovery_seconds=circuit_recovery_seconds,
                lease_seconds=distributed_lease_seconds,
            )
            if redis_client is not None
            else ProviderResilience(
                max_concurrency=max_concurrency,
                requests_per_second=requests_per_second,
                queue_timeout_seconds=queue_timeout_seconds,
                circuit_failure_threshold=circuit_failure_threshold,
                circuit_recovery_seconds=circuit_recovery_seconds,
            )
        )
        _REGISTRY[key] = resilience
    return resilience


_CLAIM_SCRIPT = """
local now = tonumber(ARGV[1])
local max_concurrency = tonumber(ARGV[2])
local lease_ms = tonumber(ARGV[3])
local token = ARGV[4]
local recovery_ms = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local open_until = tonumber(redis.call('GET', KEYS[2]) or '0')
if open_until > now then return 2 end
if open_until > 0 then
  if redis.call('SET', KEYS[3], token, 'NX', 'PX', lease_ms) == false then return 2 end
end
if redis.call('ZCARD', KEYS[1]) >= max_concurrency then
  if open_until > 0 then redis.call('DEL', KEYS[3]) end
  return 0
end
redis.call('ZADD', KEYS[1], now + lease_ms, token)
redis.call('PEXPIRE', KEYS[1], lease_ms)
return open_until > 0 and 3 or 1
"""

_RATE_SCRIPT = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local queue_ms = tonumber(ARGV[3])
local next_at = tonumber(redis.call('GET', KEYS[1]) or now)
local delay = math.max(0, next_at - now)
if delay > queue_ms then return -1 end
redis.call('SET', KEYS[1], math.max(now, next_at) + interval, 'PX', queue_ms + interval)
return delay
"""


class RedisProviderResilience(ProviderResilience):
    """使用 Redis Lua 原子脚本共享并发、限流和熔断状态。"""

    def __init__(
        self,
        redis_client: Any,
        *,
        namespace: str,
        max_concurrency: int,
        requests_per_second: float,
        queue_timeout_seconds: float,
        circuit_failure_threshold: int,
        circuit_recovery_seconds: float,
        lease_seconds: float = 900,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis_client
        self._namespace = namespace
        self._max_concurrency = max_concurrency
        self._requests_per_second = requests_per_second
        self._queue_timeout_seconds = queue_timeout_seconds
        self._failure_threshold = circuit_failure_threshold
        self._recovery_seconds = circuit_recovery_seconds
        self._lease_seconds = lease_seconds
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"{namespace}_resilience_token", default=None
        )

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self._queue_timeout_seconds
            )
        except TimeoutError as exc:
            raise ProviderResilienceError(
                "agent_provider_overloaded", "model request concurrency queue is full"
            ) from exc
        token = str(uuid4())
        now_ms = int(self._clock() * 1000)
        try:
            result = await self._redis.eval(
                _CLAIM_SCRIPT,
                3,
                self._key("active"),
                self._key("open-until"),
                self._key("probe"),
                now_ms,
                self._max_concurrency,
                int(self._lease_seconds * 1000),
                token,
                int(self._recovery_seconds * 1000),
            )
        except Exception as exc:
            self._semaphore.release()
            raise ProviderResilienceError(
                "agent_provider_guard_unavailable",
                "distributed model resilience store is unavailable",
            ) from exc
        if int(result) == 0:
            self._semaphore.release()
            raise ProviderResilienceError(
                "agent_provider_overloaded", "distributed model concurrency limit is full"
            )
        if int(result) == 2:
            self._semaphore.release()
            raise ProviderResilienceError(
                "agent_provider_circuit_open",
                "model provider circuit is open after repeated transient failures",
            )
        self._token.set(token)

    async def before_request(self) -> None:
        if self._requests_per_second <= 0:
            return
        try:
            delay_ms = await self._redis.eval(
                _RATE_SCRIPT,
                1,
                self._key("rate"),
                int(self._clock() * 1000),
                int(1000 / self._requests_per_second),
                int(self._queue_timeout_seconds * 1000),
            )
        except Exception as exc:
            raise ProviderResilienceError(
                "agent_provider_guard_unavailable",
                "distributed model resilience store is unavailable",
            ) from exc
        if int(delay_ms) < 0:
            raise ProviderResilienceError(
                "agent_provider_rate_limited", "distributed model rate limit queue is full"
            )
        if int(delay_ms) > 0:
            await asyncio.sleep(int(delay_ms) / 1000)

    async def record_success(self) -> None:
        try:
            await self._redis.delete(
                self._key("failures"), self._key("open-until"), self._key("probe")
            )
        except Exception:
            return

    async def record_failure(self, *, transient: bool) -> None:
        if not transient:
            await self.record_success()
            return
        try:
            probe = bool(await self._redis.exists(self._key("probe")))
            failures = int(await self._redis.incr(self._key("failures")))
            await self._redis.pexpire(
                self._key("failures"), int(self._recovery_seconds * 1000)
            )
            if probe or failures >= self._failure_threshold:
                await self._redis.set(
                    self._key("open-until"),
                    int(self._clock() * 1000 + self._recovery_seconds * 1000),
                    px=int(self._recovery_seconds * 2000),
                )
                await self._redis.delete(self._key("probe"))
        except Exception:
            return

    async def record_local_rejection(self) -> None:
        try:
            await self._redis.delete(self._key("probe"))
        except Exception:
            return

    async def release_async(self) -> None:
        token = self._token.get()
        try:
            if token is not None:
                await self._redis.zrem(self._key("active"), token)
        except Exception:
            pass
        finally:
            self._token.set(None)
            self._semaphore.release()

    def _key(self, suffix: str) -> str:
        return f"{self._namespace}:{suffix}"
