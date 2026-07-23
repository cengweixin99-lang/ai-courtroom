from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from mootcourt.api.dependencies import (
    RuntimeCurrentUser,
    RuntimeUnitOfWork,
    require_authenticated_principal,
)
from mootcourt.schemas.runtime import CaseSummary, CaseView, UserRole
from mootcourt.services.case_visibility import build_case_view, list_case_packages

router = APIRouter(
    prefix="/cases",
    tags=["cases"],
    dependencies=[Depends(require_authenticated_principal)],
)


@router.get(
    "",
    response_model=list[CaseSummary],
    operation_id="list_case_packages",
    summary="列出可用案件包",
    response_description="已导入运行库的案件包版本列表",
)
async def list_cases(
    unit_of_work: RuntimeUnitOfWork, current_user: RuntimeCurrentUser
) -> list[CaseSummary]:
    """列出运行库中的案件包版本，不读取创作目录或标准答案文件。"""
    allowed_package_ids = await unit_of_work.identity.accessible_package_ids(current_user.id)
    return await list_case_packages(unit_of_work, accessible_package_ids=allowed_package_ids)


@router.get(
    "/{case_id}",
    response_model=CaseView,
    operation_id="get_role_scoped_case",
    summary="获取角色可见的案件内容",
    response_description="按指定庭审角色过滤后的案件视图",
    responses={404: {"description": "案件或指定版本不存在"}},
)
async def get_case(
    case_id: Annotated[str, Path(description="案件包的稳定业务标识")],
    unit_of_work: RuntimeUnitOfWork,
    current_user: RuntimeCurrentUser,
    role: Annotated[UserRole, Query(description="请求方扮演的庭审角色")],
    package_version: Annotated[
        str | None,
        Query(description="指定案件包版本；不传时读取最新导入版本"),
    ] = None,
) -> CaseView:
    """返回角色隔离后的案件视图。

    过滤在 Service 层统一完成。响应不会包含其他角色的材料、禁止公开的事实，
    也不会读取创作阶段使用的 ground-truth 文件。
    """
    package = await unit_of_work.case_packages.get_runtime_package(case_id, package_version)
    if package is None or not await unit_of_work.identity.can_access_case(
        current_user.id, package.id
    ):
        raise HTTPException(status_code=404, detail={"code": "case_not_found"})
    view = await build_case_view(unit_of_work, case_id, role, package_version)
    if view is None:
        raise HTTPException(status_code=404, detail={"code": "case_not_found"})
    return view
