from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mootcourt.core.config import get_settings
from mootcourt.core.security import DIAGNOSTICS_KEY_HEADER, diagnostics_access_allowed

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SESSION_PATH_PATTERN = re.compile(r"/sessions/([^/]+)")
_UNKNOWN_ROUTE = "unmatched"

HTTP_REQUESTS_TOTAL = Counter(
    "mootcourt_http_requests_total",
    "Total number of completed HTTP requests.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "mootcourt_http_request_duration_seconds",
    "End-to-end HTTP request duration, including streamed response bodies.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "mootcourt_http_requests_in_progress",
    "Number of HTTP requests currently being served.",
    ("method",),
)
AGENT_TURNS_TOTAL = Counter(
    "mootcourt_agent_turns_total",
    "Total number of persisted Agent turn results.",
    ("actor_role", "action", "status", "error_code", "provider", "model"),
)
AGENT_TURN_DURATION_SECONDS = Histogram(
    "mootcourt_agent_turn_duration_seconds",
    "Agent provider and validation duration for persisted turn results.",
    ("actor_role", "action", "status", "provider", "model"),
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300),
)
AGENT_TOKENS_TOTAL = Counter(
    "mootcourt_agent_tokens_total",
    "Model tokens consumed by persisted Agent turn results.",
    ("provider", "model", "direction"),
)
AGENT_REPAIRS_TOTAL = Counter(
    "mootcourt_agent_repairs_total",
    "Structured output repair attempts used by Agent turns.",
    ("provider", "model"),
)
AGENT_OUTPUT_NORMALIZATIONS_TOTAL = Counter(
    "mootcourt_agent_output_normalizations_total",
    "Agent outputs normalized by deterministic compatibility rules.",
    ("provider", "model"),
)
AGENT_PROVIDER_RETRIES_TOTAL = Counter(
    "mootcourt_agent_provider_retries_total",
    "Agent provider retries by bounded failure reason.",
    ("provider", "model", "reason"),
)
AGENT_PROVIDER_GUARD_REJECTIONS_TOTAL = Counter(
    "mootcourt_agent_provider_guard_rejections_total",
    "Agent provider calls rejected by local concurrency, rate, or circuit guards.",
    ("provider", "model", "reason"),
)

logger = structlog.get_logger(__name__)


class ObservabilityMiddleware:
    """为每个请求建立日志上下文，并记录包含 SSE 生命周期的访问指标。"""

    def __init__(self, app: ASGIApp, *, access_log_enabled: bool = True) -> None:
        self._app = app
        self._access_log_enabled = access_log_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        method = str(scope.get("method", "UNKNOWN")).upper()
        path = str(scope.get("path", ""))
        request_id = _request_id(Headers(scope=scope).get("X-Request-ID"))
        session_id = _session_id(path)
        started = perf_counter()
        status_code = 500
        completed = False
        cancelled = False

        structlog.contextvars.clear_contextvars()
        context: dict[str, str] = {"request_id": request_id}
        if session_id is not None:
            context["session_id"] = session_id
        structlog.contextvars.bind_contextvars(**context)
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()

        if self._access_log_enabled:
            logger.info("http_request_started", method=method)

        async def send_with_observability(message: Message) -> None:
            nonlocal status_code, completed
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True
            await send(message)

        try:
            await self._app(scope, receive, send_with_observability)
        except asyncio.CancelledError:
            cancelled = True
            status_code = 499
            raise
        except Exception as exc:
            status_code = 500
            logger.exception(
                "http_request_unhandled_error",
                method=method,
                route=_route_template(scope),
                exception_type=type(exc).__name__,
            )
            raise
        finally:
            duration_seconds = max(0.0, perf_counter() - started)
            route = _route_template(scope)
            # 未完整发送响应通常代表客户端断开；用 499 与服务端 5xx 区分。
            if not completed and status_code < 400:
                status_code = 499
            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                route=route,
                status_code=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(
                duration_seconds
            )
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()
            if self._access_log_enabled:
                log_method: Callable[..., Any] = (
                    logger.warning if cancelled or status_code >= 500 else logger.info
                )
                log_method(
                    "http_request_completed",
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_ms=round(duration_seconds * 1_000),
                    completed=completed,
                )
            structlog.contextvars.clear_contextvars()


def metrics_response(request: Request) -> Response:
    """返回 Prometheus 文本格式；指标中不包含请求或会话唯一标识。"""
    if not diagnostics_access_allowed(request.headers.get(DIAGNOSTICS_KEY_HEADER), get_settings()):
        return JSONResponse(
            status_code=401,
            content={
                "detail": {
                    "code": "diagnostics_auth_required",
                    "message": "valid diagnostics credentials are required",
                }
            },
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_agent_turn(
    *,
    actor_role: str,
    action: str,
    status: str,
    error_code: str | None,
    provider: str,
    model: str,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    repair_count: int,
    output_normalized: bool,
) -> None:
    """记录低基数 Agent 指标；调用方须在 Trace 持久化后调用。"""

    normalized_error = error_code or "none"
    AGENT_TURNS_TOTAL.labels(
        actor_role=actor_role,
        action=action,
        status=status,
        error_code=normalized_error,
        provider=provider,
        model=model,
    ).inc()
    AGENT_TURN_DURATION_SECONDS.labels(
        actor_role=actor_role,
        action=action,
        status=status,
        provider=provider,
        model=model,
    ).observe(max(0, latency_ms) / 1_000)
    AGENT_TOKENS_TOTAL.labels(provider=provider, model=model, direction="input").inc(
        max(0, input_tokens)
    )
    AGENT_TOKENS_TOTAL.labels(provider=provider, model=model, direction="output").inc(
        max(0, output_tokens)
    )
    if repair_count > 0:
        AGENT_REPAIRS_TOTAL.labels(provider=provider, model=model).inc(repair_count)
    if output_normalized:
        AGENT_OUTPUT_NORMALIZATIONS_TOTAL.labels(provider=provider, model=model).inc()


def record_provider_retry(*, provider: str, model: str, reason: str) -> None:
    """记录 Provider 重试；reason 必须由调用方使用固定枚举，禁止写入上游错误文本。"""

    AGENT_PROVIDER_RETRIES_TOTAL.labels(
        provider=provider,
        model=model,
        reason=reason,
    ).inc()


def record_provider_guard_rejection(*, provider: str, model: str, reason: str) -> None:
    AGENT_PROVIDER_GUARD_REJECTIONS_TOTAL.labels(
        provider=provider,
        model=model,
        reason=reason,
    ).inc()


def _request_id(value: str | None) -> str:
    if value is not None and _REQUEST_ID_PATTERN.fullmatch(value) is not None:
        return value
    return str(uuid4())


def _session_id(path: str) -> str | None:
    match = _SESSION_PATH_PATTERN.search(path)
    return match.group(1) if match is not None else None


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    if route is None:
        return _UNKNOWN_ROUTE
    path = scope.get("path")
    if not isinstance(path, str) or not path:
        return _UNKNOWN_ROUTE
    # FastAPI 的嵌套路由在 scope 中可能只保留子路由模板；从实际路径替换已解析参数，
    # 既保留 /api/v1 前缀，也避免把 session_id 等唯一值写进指标标签。
    path_params = scope.get("path_params")
    if isinstance(path_params, dict):
        for name, value in path_params.items():
            pattern = rf"(?<=/){re.escape(str(value))}(?=/|$)"
            path = re.sub(pattern, f"{{{name}}}", path, count=1)
    return path
