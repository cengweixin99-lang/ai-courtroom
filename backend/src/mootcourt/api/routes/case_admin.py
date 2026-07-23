from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi import Path as ApiPath

from mootcourt.api.dependencies import (
    RuntimeCurrentUser,
    RuntimeUnitOfWork,
    require_authenticated_principal,
)
from mootcourt.core.config import Settings, get_settings
from mootcourt.schemas.case_admin import (
    CaseImportAttemptView,
    CaseImportIssue,
    ManagedCasePackageView,
    ManagedOrganizationView,
    PublishCasePackageRequest,
)
from mootcourt.services.case_admin import (
    CaseAdminError,
    import_case_archive,
    list_managed_case_packages,
    publish_case_package,
    record_rejected_case_import,
)

router = APIRouter(
    prefix="/admin/case-packages",
    tags=["case-admin"],
    dependencies=[Depends(require_authenticated_principal)],
)
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/organizations",
    response_model=list[ManagedOrganizationView],
    operation_id="list_case_admin_organizations",
    summary="列出当前管理员可发布案件的组织",
    response_description="仅返回当前用户在 MySQL 中担任管理员的组织",
)
async def list_case_admin_organizations(
    unit_of_work: RuntimeUnitOfWork, current_user: RuntimeCurrentUser
) -> list[ManagedOrganizationView]:
    """为发布表单提供服务端授权范围，前端不硬编码任何组织。"""
    organizations = await unit_of_work.identity.list_administrated_organizations(current_user.id)
    return [
        ManagedOrganizationView(id=item.id, slug=item.slug, name=item.name)
        for item in organizations
    ]


@router.get(
    "",
    response_model=list[ManagedCasePackageView],
    operation_id="list_managed_case_packages",
    summary="列出管理员可管理的案件版本",
    response_description="包含草稿、发布状态和组织授权范围的案件版本列表",
    responses={403: {"description": "当前用户不是组织管理员"}},
)
async def list_managed_cases(
    unit_of_work: RuntimeUnitOfWork, current_user: RuntimeCurrentUser
) -> list[ManagedCasePackageView]:
    """返回上传者或当前组织管理员有权管理的草稿与已发布案件版本。"""
    try:
        return await list_managed_case_packages(unit_of_work, current_user.id)
    except CaseAdminError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/imports",
    response_model=CaseImportAttemptView,
    status_code=201,
    operation_id="import_case_package_archive",
    summary="上传并校验案件 ZIP",
    response_description="安全校验后创建草稿案件，并返回可定位的导入问题",
    responses={
        403: {"description": "当前用户不是组织管理员"},
        413: {"description": "ZIP 大小超过配置限制"},
        415: {"description": "请求体不是 application/zip"},
        422: {"description": "ZIP 或案件包内容校验失败"},
    },
)
async def import_case_package_zip(
    request: Request,
    response: Response,
    unit_of_work: RuntimeUnitOfWork,
    current_user: RuntimeCurrentUser,
    settings: AppSettings,
    source_filename: Annotated[str, Header(alias="X-Filename", min_length=1, max_length=255)],
) -> CaseImportAttemptView:
    """流式接收 ZIP，在写入草稿库前执行路径、容量和案卷 Schema 校验。"""
    await _require_case_admin(unit_of_work, current_user.id)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/zip":
        response.status_code = 415
        return await record_rejected_case_import(
            unit_of_work,
            actor_user_id=current_user.id,
            source_filename=source_filename,
            source_sha256=None,
            archive_size_bytes=0,
            issue=CaseImportIssue(
                code="case_archive_content_type_invalid",
                message="请求体必须使用 application/zip",
            ),
        )
    decoded_filename = unquote(source_filename)
    safe_filename = Path(decoded_filename).name
    if (
        safe_filename != decoded_filename
        or "/" in decoded_filename
        or "\\" in decoded_filename
        or any(ord(character) < 32 for character in decoded_filename)
        or not safe_filename.lower().endswith(".zip")
    ):
        response.status_code = 422
        return await record_rejected_case_import(
            unit_of_work,
            actor_user_id=current_user.id,
            source_filename=source_filename,
            source_sha256=None,
            archive_size_bytes=0,
            issue=CaseImportIssue(
                code="case_archive_filename_invalid",
                message="X-Filename 必须是无路径分隔符的 ZIP 文件名",
            ),
        )

    digest = hashlib.sha256()
    received = 0
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="mootcourt-upload-", suffix=".zip", delete=False
        ) as output:
            temporary_path = Path(output.name)
            async for chunk in request.stream():
                received += len(chunk)
                digest.update(chunk)
                if received > settings.case_import_max_archive_bytes:
                    result = await record_rejected_case_import(
                        unit_of_work,
                        actor_user_id=current_user.id,
                        source_filename=safe_filename,
                        source_sha256=None,
                        archive_size_bytes=received,
                        issue=CaseImportIssue(
                            code="case_archive_too_large",
                            message="ZIP 大小超过服务器限制",
                        ),
                    )
                    response.status_code = 413
                    return result
                output.write(chunk)

        result = await import_case_archive(
            unit_of_work,
            actor_user_id=current_user.id,
            archive_path=temporary_path,
            source_filename=safe_filename,
            archive_size_bytes=received,
            settings=settings,
        )
        response.status_code = {"accepted": 201, "duplicate": 200, "rejected": 422}[result.status]
        return result
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.post(
    "/{database_id}/publish",
    response_model=ManagedCasePackageView,
    operation_id="publish_case_package",
    summary="发布草稿案件并授权组织",
    response_description="原子完成案件发布和组织访问授权，既有庭审版本不受影响",
    responses={
        403: {"description": "无权管理案件或目标组织"},
        404: {"description": "案件版本不存在"},
    },
)
async def publish_case(
    database_id: Annotated[int, ApiPath(ge=1, description="案件版本数据库 ID")],
    request: PublishCasePackageRequest,
    unit_of_work: RuntimeUnitOfWork,
    current_user: RuntimeCurrentUser,
) -> ManagedCasePackageView:
    """将已校验草稿发布给管理员所属组织，并保留版本和导入审计。"""
    try:
        return await publish_case_package(
            unit_of_work,
            actor_user_id=current_user.id,
            database_id=database_id,
            organization_ids=request.organization_ids,
        )
    except CaseAdminError as exc:
        raise _http_error(exc) from exc


async def _require_case_admin(unit_of_work: RuntimeUnitOfWork, user_id: int) -> None:
    if await unit_of_work.identity.administrated_organization_ids(user_id):
        return
    raise HTTPException(status_code=403, detail={"code": "case_admin_required"})


def _http_error(exc: CaseAdminError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
    )
