from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mootcourt.services.organization_admin import (
    OrganizationAdminError,
    list_organization_members,
    remove_organization_member,
    set_organization_member,
)


def _identity(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "administrated_organization_ids": AsyncMock(return_value={"org-1"}),
        "list_organization_members": AsyncMock(return_value=[]),
        "list_platform_users": AsyncMock(
            return_value=[SimpleNamespace(id=7, email="user@example.test", display_name="User")]
        ),
        "get_platform_user": AsyncMock(return_value=SimpleNamespace(id=7)),
        "get_membership": AsyncMock(return_value=None),
        "count_organization_admins": AsyncMock(return_value=1),
        "set_membership_role": AsyncMock(),
        "remove_membership": AsyncMock(),
        **overrides,
    }
    return SimpleNamespace(**values)


async def test_list_members_returns_directory_users_not_already_in_org() -> None:
    member_user = SimpleNamespace(
        id=5,
        email="admin@example.test",
        display_name="Admin",
    )
    identity = _identity(
        list_organization_members=AsyncMock(
            return_value=[
                SimpleNamespace(
                    user_id=5,
                    user=member_user,
                    role="admin",
                    created_at=datetime.now(UTC),
                )
            ]
        ),
        list_platform_users=AsyncMock(
            return_value=[
                member_user,
                SimpleNamespace(id=7, email="user@example.test", display_name="User"),
            ]
        ),
    )

    result = await list_organization_members(
        SimpleNamespace(identity=identity), actor_user_id=1, organization_id="org-1"
    )

    assert result.organization_id == "org-1"
    assert result.members[0].role == "admin"
    assert result.available_users[0].email == "user@example.test"


async def test_set_member_rejects_unknown_user() -> None:
    identity = _identity(get_platform_user=AsyncMock(return_value=None))

    with pytest.raises(OrganizationAdminError, match="用户不存在") as raised:
        await set_organization_member(
            SimpleNamespace(identity=identity),
            actor_user_id=1,
            organization_id="org-1",
            user_id=99,
            role="learner",
        )

    assert raised.value.code == "organization_user_not_found"


async def test_set_member_protects_last_admin_even_for_a_consistent_admin_scope() -> None:
    identity = _identity(
        get_membership=AsyncMock(return_value=SimpleNamespace(role="admin")),
        count_organization_admins=AsyncMock(return_value=1),
    )

    with pytest.raises(OrganizationAdminError) as raised:
        await set_organization_member(
            SimpleNamespace(identity=identity),
            actor_user_id=1,
            organization_id="org-1",
            user_id=7,
            role="learner",
        )

    assert raised.value.code == "last_organization_admin_protected"


async def test_remove_member_reports_missing_membership() -> None:
    identity = _identity(get_membership=AsyncMock(return_value=None))

    with pytest.raises(OrganizationAdminError) as raised:
        await remove_organization_member(
            SimpleNamespace(identity=identity),
            actor_user_id=1,
            organization_id="org-1",
            user_id=7,
        )

    assert raised.value.code == "organization_member_not_found"


async def test_admin_cannot_downgrade_self() -> None:
    identity = _identity(
        get_membership=AsyncMock(return_value=SimpleNamespace(role="admin")),
    )

    with pytest.raises(OrganizationAdminError) as raised:
        await set_organization_member(
            SimpleNamespace(identity=identity),
            actor_user_id=7,
            organization_id="org-1",
            user_id=7,
            role="learner",
        )

    assert raised.value.code == "organization_self_role_change_forbidden"


async def test_admin_cannot_remove_self() -> None:
    identity = _identity(
        get_membership=AsyncMock(return_value=SimpleNamespace(role="admin")),
    )

    with pytest.raises(OrganizationAdminError) as raised:
        await remove_organization_member(
            SimpleNamespace(identity=identity),
            actor_user_id=7,
            organization_id="org-1",
            user_id=7,
        )

    assert raised.value.code == "organization_self_removal_forbidden"


async def test_member_management_is_tenant_scoped() -> None:
    identity = _identity(
        administrated_organization_ids=AsyncMock(return_value=set()),
    )

    with pytest.raises(OrganizationAdminError) as raised:
        await list_organization_members(
            SimpleNamespace(identity=identity), actor_user_id=7, organization_id="other-org"
        )

    assert raised.value.code == "organization_admin_required"
