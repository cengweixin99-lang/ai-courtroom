from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi import Path as ApiPath

from mootcourt.api.dependencies import RuntimeCurrentUser, RuntimeUnitOfWork
from mootcourt.schemas.case_admin import OrganizationMembersView, SetOrganizationMemberRequest
from mootcourt.services.organization_admin import (
    OrganizationAdminError,
    list_organization_members,
    remove_organization_member,
    set_organization_member,
)

router = APIRouter(
    prefix="/admin/organizations",
    tags=["organization-admin"],
)


@router.get(
    "/{organization_id}/members",
    response_model=OrganizationMembersView,
    operation_id="list_organization_members",
    summary="查看组织成员和可授权用户",
)
async def get_organization_members(
    organization_id: Annotated[str, ApiPath(min_length=1, max_length=36)],
    unit_of_work: RuntimeUnitOfWork,
    current_user: RuntimeCurrentUser,
) -> OrganizationMembersView:
    """返回组织成员，以及已经登录但尚未加入该组织的用户。"""
    try:
        return await list_organization_members(
            unit_of_work, actor_user_id=current_user.id, organization_id=organization_id
        )
    except OrganizationAdminError as exc:
        raise _http_error(exc) from exc


@router.put(
    "/{organization_id}/members/{user_id}",
    response_model=OrganizationMembersView,
    operation_id="set_organization_member",
    summary="添加或修改组织成员角色",
)
async def put_organization_member(
    organization_id: Annotated[str, ApiPath(min_length=1, max_length=36)],
    user_id: Annotated[int, ApiPath(ge=1)],
    request: SetOrganizationMemberRequest,
    unit_of_work: RuntimeUnitOfWork,
    current_user: RuntimeCurrentUser,
) -> OrganizationMembersView:
    """幂等添加成员或修改角色，并执行管理员安全约束。"""
    try:
        return await set_organization_member(
            unit_of_work,
            actor_user_id=current_user.id,
            organization_id=organization_id,
            user_id=user_id,
            role=request.role,
        )
    except OrganizationAdminError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/{organization_id}/members/{user_id}",
    response_model=OrganizationMembersView,
    operation_id="remove_organization_member",
    summary="移除组织成员",
)
async def delete_organization_member(
    organization_id: Annotated[str, ApiPath(min_length=1, max_length=36)],
    user_id: Annotated[int, ApiPath(ge=1)],
    unit_of_work: RuntimeUnitOfWork,
    current_user: RuntimeCurrentUser,
) -> OrganizationMembersView:
    """移除组织成员，同时禁止管理员移除自己或最后一名管理员。"""
    try:
        return await remove_organization_member(
            unit_of_work,
            actor_user_id=current_user.id,
            organization_id=organization_id,
            user_id=user_id,
        )
    except OrganizationAdminError as exc:
        raise _http_error(exc) from exc


def _http_error(exc: OrganizationAdminError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )
