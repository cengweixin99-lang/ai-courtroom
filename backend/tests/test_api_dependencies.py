from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from mootcourt.api import dependencies
from mootcourt.core.auth import AuthenticatedPrincipal, AuthenticationError
from mootcourt.core.config import Settings
from mootcourt.db.models import PlatformUserModel


async def test_unit_of_work_commits_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    unit_of_work = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(dependencies, "get_session_factory", lambda: session_context)
    monkeypatch.setattr(dependencies, "SqlAlchemyUnitOfWork", lambda _session: unit_of_work)
    generator = dependencies.get_unit_of_work()

    assert await anext(generator) is unit_of_work
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    unit_of_work.commit.assert_awaited_once()
    unit_of_work.rollback.assert_not_awaited()


async def test_unit_of_work_rolls_back_after_error(monkeypatch: pytest.MonkeyPatch) -> None:
    unit_of_work = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    @asynccontextmanager
    async def session_context():
        yield object()

    monkeypatch.setattr(dependencies, "get_session_factory", lambda: session_context)
    monkeypatch.setattr(dependencies, "SqlAlchemyUnitOfWork", lambda _session: unit_of_work)
    generator = dependencies.get_unit_of_work()
    await anext(generator)

    with pytest.raises(RuntimeError, match="route failed"):
        await generator.athrow(RuntimeError("route failed"))

    unit_of_work.rollback.assert_awaited_once()
    unit_of_work.commit.assert_not_awaited()


async def test_authentication_dependency_returns_verified_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = AuthenticatedPrincipal(
        subject="verified-user",
        email=None,
        provider_role="authenticated",
        claims={"sub": "verified-user"},
    )
    authenticate = AsyncMock(return_value=principal)
    monkeypatch.setattr(dependencies, "authenticate_bearer_token", authenticate)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")

    result = await dependencies.require_authenticated_principal(Settings(), credentials)

    assert result is principal
    authenticate.assert_awaited_once_with("signed-token", ANY)


async def test_authentication_dependency_converts_failure_to_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dependencies,
        "authenticate_bearer_token",
        AsyncMock(side_effect=AuthenticationError("expired")),
    )

    with pytest.raises(HTTPException) as raised:
        await dependencies.require_authenticated_principal(Settings(), None)

    assert raised.value.status_code == 401
    assert raised.value.detail == {"code": "authentication_required", "message": "expired"}
    assert raised.value.headers == {"WWW-Authenticate": "Bearer"}


async def test_bootstrap_subject_is_promoted_from_server_allowlist() -> None:
    user = PlatformUserModel(id=7, auth_subject="bootstrap-user", email="admin@example.test")
    identity = SimpleNamespace(
        get_or_create_user=AsyncMock(return_value=user),
        ensure_public_admin=AsyncMock(),
    )
    principal = AuthenticatedPrincipal(
        subject="bootstrap-user",
        email="admin@example.test",
        provider_role="authenticated",
        claims={"sub": "bootstrap-user", "role": "learner"},
    )

    result = await dependencies.get_current_user(
        principal,
        SimpleNamespace(identity=identity),
        Settings(auth_bootstrap_admin_subjects=["bootstrap-user"]),
    )

    assert result is user
    identity.get_or_create_user.assert_awaited_once_with("bootstrap-user", "admin@example.test")
    identity.ensure_public_admin.assert_awaited_once_with(7)


async def test_session_access_allows_owner_and_rejects_cross_tenant_user() -> None:
    owner = PlatformUserModel(id=10, auth_subject="owner")
    outsider = PlatformUserModel(id=20, auth_subject="outsider")
    court_session = SimpleNamespace(owner_user_id=10)
    unit_of_work = SimpleNamespace(
        court_sessions=SimpleNamespace(get=AsyncMock(return_value=court_session)),
        identity=SimpleNamespace(can_manage_user_sessions=AsyncMock(return_value=False)),
    )

    await dependencies.require_session_access(unit_of_work, owner, "SESSION-1")
    with pytest.raises(HTTPException) as raised:
        await dependencies.require_session_access(unit_of_work, outsider, "SESSION-1")

    assert raised.value.status_code == 403
    assert raised.value.detail == {"code": "session_access_denied"}


async def test_session_access_returns_404_without_disclosing_authorization() -> None:
    user = PlatformUserModel(id=10, auth_subject="user")
    unit_of_work = SimpleNamespace(
        court_sessions=SimpleNamespace(get=AsyncMock(return_value=None)),
        identity=SimpleNamespace(can_manage_user_sessions=AsyncMock()),
    )

    with pytest.raises(HTTPException) as raised:
        await dependencies.require_session_access(unit_of_work, user, "MISSING")

    assert raised.value.status_code == 404
    assert raised.value.detail == {"code": "session_not_found"}


async def test_session_access_allows_instructor_for_member_session() -> None:
    instructor = PlatformUserModel(id=20, auth_subject="instructor")
    unit_of_work = SimpleNamespace(
        court_sessions=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(owner_user_id=10))
        ),
        identity=SimpleNamespace(can_manage_user_sessions=AsyncMock(return_value=True)),
    )

    await dependencies.require_session_access(unit_of_work, instructor, "SESSION-1")

    unit_of_work.identity.can_manage_user_sessions.assert_awaited_once_with(20, 10)
