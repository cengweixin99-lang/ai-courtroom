from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
_RUNNING = "running"
_COMPLETED = "completed"
_ABANDONED = "abandoned"


class AgentInvocationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AgentInvocationLease:
    invocation_id: str
    idempotency_key: str
    operation: str
    lease_token: str
    replayed_payload: dict[str, Any] | None = None

    @property
    def replayed(self) -> bool:
        return self.replayed_payload is not None


async def acquire_agent_invocation(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    operation: str,
    idempotency_key: str | None,
    request_payload: dict[str, Any],
    lease_seconds: int,
) -> AgentInvocationLease:
    key = _validated_key(idempotency_key)
    fingerprint = _request_fingerprint(operation, request_payload)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=lease_seconds)

    # 只在争抢租约时锁会话行并立即提交，绝不在外部 LLM 调用期间持有数据库锁。
    session = await unit_of_work.court_sessions.get_for_update(session_id)
    if session is None:
        raise AgentInvocationError("session_not_found", "court session not found", 404)

    existing = await unit_of_work.agent_invocations.get_by_key(session_id, key)
    if existing is not None:
        if existing.operation != operation or existing.request_fingerprint != fingerprint:
            raise AgentInvocationError(
                "idempotency_key_reused",
                "idempotency key was already used for a different request",
            )
        if existing.status == _COMPLETED:
            if not isinstance(existing.response_payload, dict):
                raise AgentInvocationError(
                    "idempotency_result_unavailable",
                    "completed idempotent request has no reusable response",
                    500,
                )
            return AgentInvocationLease(
                invocation_id=existing.id,
                idempotency_key=key,
                operation=operation,
                lease_token=existing.lease_token,
                replayed_payload=existing.response_payload,
            )

    active_id = session.active_agent_invocation_id
    active_expires_at = _aware_utc(session.active_agent_lease_expires_at)
    if active_id is not None and active_expires_at is not None and active_expires_at > now:
        if existing is None or active_id != existing.id:
            raise AgentInvocationError(
                "agent_invocation_in_progress",
                "another Agent invocation is already running for this session",
            )
        raise AgentInvocationError(
            "agent_invocation_in_progress",
            "this idempotent Agent invocation is still running",
        )

    if active_id is not None:
        expired = await unit_of_work.agent_invocations.get_for_update(active_id)
        if expired is not None and expired.status == _RUNNING:
            expired.status = _ABANDONED
            expired.error_code = "agent_invocation_lease_expired"

    lease_token = str(uuid4())
    if existing is None:
        existing = unit_of_work.agent_invocations.add(
            session_id=session_id,
            operation=operation,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status=_RUNNING,
            lease_token=lease_token,
            lease_expires_at=expires_at,
        )
        await unit_of_work.agent_invocations.flush()
    else:
        existing.status = _RUNNING
        existing.lease_token = lease_token
        existing.lease_expires_at = expires_at
        existing.response_payload = None
        existing.error_code = None

    session.active_agent_invocation_id = existing.id
    session.active_agent_lease_token = lease_token
    session.active_agent_lease_expires_at = expires_at
    return AgentInvocationLease(
        invocation_id=existing.id,
        idempotency_key=key,
        operation=operation,
        lease_token=lease_token,
    )


async def complete_agent_invocation(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    lease: AgentInvocationLease,
    response_payload: dict[str, Any],
) -> None:
    session = await unit_of_work.court_sessions.get_for_update(session_id)
    invocation = await unit_of_work.agent_invocations.get_for_update(lease.invocation_id)
    if session is None or invocation is None:
        raise AgentInvocationError(
            "agent_invocation_missing", "Agent invocation lease no longer exists", 500
        )
    if (
        session.active_agent_invocation_id != lease.invocation_id
        or session.active_agent_lease_token != lease.lease_token
        or invocation.lease_token != lease.lease_token
    ):
        raise AgentInvocationError(
            "agent_invocation_lease_lost",
            "Agent invocation lease was replaced before completion",
        )

    invocation.status = _COMPLETED
    invocation.response_payload = response_payload
    invocation.error_code = None
    _clear_session_lease(session)


async def abandon_agent_invocation(
    unit_of_work: SqlAlchemyUnitOfWork,
    session_id: str,
    lease: AgentInvocationLease,
    error_code: str,
) -> None:
    session = await unit_of_work.court_sessions.get_for_update(session_id)
    invocation = await unit_of_work.agent_invocations.get_for_update(lease.invocation_id)
    if session is None or invocation is None:
        return
    # 旧 worker 完成时不得清除已经被新请求接管的租约。
    if (
        session.active_agent_invocation_id != lease.invocation_id
        or session.active_agent_lease_token != lease.lease_token
        or invocation.lease_token != lease.lease_token
    ):
        return
    invocation.status = _ABANDONED
    invocation.error_code = error_code
    _clear_session_lease(session)


def _validated_key(value: str | None) -> str:
    key = value or str(uuid4())
    if not 8 <= len(key) <= 128 or _IDEMPOTENCY_KEY_PATTERN.fullmatch(key) is None:
        raise AgentInvocationError(
            "idempotency_key_invalid",
            "Idempotency-Key must be 8-128 ASCII letters, digits, '.', '_', ':' or '-'",
            422,
        )
    return key


def _request_fingerprint(operation: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clear_session_lease(session: Any) -> None:
    session.active_agent_invocation_id = None
    session.active_agent_lease_token = None
    session.active_agent_lease_expires_at = None
