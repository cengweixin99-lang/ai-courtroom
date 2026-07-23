from __future__ import annotations

from typing import cast

from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_admin import (
    OrganizationDirectoryUserView,
    OrganizationMemberRole,
    OrganizationMembersView,
    OrganizationMemberView,
)


class OrganizationAdminError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def list_organization_members(
    unit_of_work: SqlAlchemyUnitOfWork, *, actor_user_id: int, organization_id: str
) -> OrganizationMembersView:
    await _require_organization_admin(unit_of_work, actor_user_id, organization_id)
    members = await unit_of_work.identity.list_organization_members(organization_id)
    member_ids = {item.user_id for item in members}
    users = await unit_of_work.identity.list_platform_users()
    return OrganizationMembersView(
        organization_id=organization_id,
        members=[
            OrganizationMemberView(
                user_id=item.user_id,
                email=item.user.email,
                display_name=item.user.display_name,
                role=cast(OrganizationMemberRole, item.role),
                created_at=item.created_at,
            )
            for item in members
        ],
        available_users=[
            OrganizationDirectoryUserView(
                user_id=item.id,
                email=item.email,
                display_name=item.display_name,
            )
            for item in users
            if item.id not in member_ids
        ],
    )


async def set_organization_member(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    organization_id: str,
    user_id: int,
    role: OrganizationMemberRole,
) -> OrganizationMembersView:
    await _require_organization_admin(unit_of_work, actor_user_id, organization_id)
    if await unit_of_work.identity.get_platform_user(user_id) is None:
        raise OrganizationAdminError("organization_user_not_found", "用户不存在", 404)
    existing = await unit_of_work.identity.get_membership(organization_id, user_id)
    if (
        existing is not None
        and existing.role == "admin"
        and user_id == actor_user_id
        and role != "admin"
    ):
        raise OrganizationAdminError(
            "organization_self_role_change_forbidden",
            "管理员不能降级自己的组织角色，请先指定其他管理员",
            409,
        )
    if (
        existing is not None
        and existing.role == "admin"
        and role != "admin"
        and await unit_of_work.identity.count_organization_admins(organization_id) <= 1
    ):
        raise OrganizationAdminError(
            "last_organization_admin_protected",
            "组织至少需要保留一名管理员",
            409,
        )
    await unit_of_work.identity.set_membership_role(organization_id, user_id, role)
    return await list_organization_members(
        unit_of_work, actor_user_id=actor_user_id, organization_id=organization_id
    )


async def remove_organization_member(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    organization_id: str,
    user_id: int,
) -> OrganizationMembersView:
    await _require_organization_admin(unit_of_work, actor_user_id, organization_id)
    existing = await unit_of_work.identity.get_membership(organization_id, user_id)
    if existing is None:
        raise OrganizationAdminError("organization_member_not_found", "组织成员不存在", 404)
    if user_id == actor_user_id:
        raise OrganizationAdminError(
            "organization_self_removal_forbidden",
            "管理员不能移除自己，请由其他管理员操作",
            409,
        )
    if (
        existing.role == "admin"
        and await unit_of_work.identity.count_organization_admins(organization_id) <= 1
    ):
        raise OrganizationAdminError(
            "last_organization_admin_protected", "组织至少需要保留一名管理员", 409
        )
    await unit_of_work.identity.remove_membership(organization_id, user_id)
    return await list_organization_members(
        unit_of_work, actor_user_id=actor_user_id, organization_id=organization_id
    )


async def _require_organization_admin(
    unit_of_work: SqlAlchemyUnitOfWork, actor_user_id: int, organization_id: str
) -> None:
    if organization_id not in await unit_of_work.identity.administrated_organization_ids(
        actor_user_id
    ):
        raise OrganizationAdminError("organization_admin_required", "无权管理该组织", 403)
