from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError
from sqlalchemy import func, select

from mootcourt.core.config import Settings
from mootcourt.db.models import CaseImportAttemptModel, CasePackageModel
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_admin import (
    CaseImportAttemptView,
    CaseImportIssue,
    ManagedCasePackageView,
)
from mootcourt.schemas.case_package import load_case_package
from mootcourt.db.models import CourtSessionModel
from mootcourt.services.case_archive import (
    CaseArchiveError,
    extract_case_archive,
    validate_package_file_manifest,
)


class CaseAdminError(ValueError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


async def import_case_archive(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    archive_path: Path,
    source_filename: str,
    archive_size_bytes: int,
    settings: Settings,
) -> CaseImportAttemptView:
    source_sha256 = _sha256(archive_path)
    try:
        with tempfile.TemporaryDirectory(prefix="mootcourt-case-import-") as directory:
            package_root = extract_case_archive(
                archive_path,
                Path(directory),
                max_files=settings.case_import_max_files,
                max_uncompressed_bytes=settings.case_import_max_uncompressed_bytes,
                max_compression_ratio=settings.case_import_max_compression_ratio,
            )
            package = load_case_package(package_root)
            validate_package_file_manifest(package_root, package.manifest.files)
    except CaseArchiveError as exc:
        return await _rejected_attempt(
            unit_of_work,
            actor_user_id,
            source_filename,
            source_sha256,
            archive_size_bytes,
            [exc.issue],
        )
    except (ValidationError, ValueError) as exc:
        return await _rejected_attempt(
            unit_of_work,
            actor_user_id,
            source_filename,
            source_sha256,
            archive_size_bytes,
            _validation_issues(exc, package_root),
        )

    model, created = await unit_of_work.case_packages.add_if_absent(
        package,
        lifecycle_status="draft",
        source_filename=source_filename,
        source_sha256=source_sha256,
        uploaded_by_user_id=actor_user_id,
    )
    if not created:
        existing = model
        if existing.source_sha256 and existing.source_sha256 != source_sha256:
            return await _rejected_attempt(
                unit_of_work,
                actor_user_id,
                source_filename,
                source_sha256,
                archive_size_bytes,
                [
                    CaseImportIssue(
                        code="case_package_version_conflict",
                        message="相同案件 ID 和版本已存在，但 ZIP 内容哈希不同",
                    )
                ],
            )
        attempt = await unit_of_work.case_packages.add_import_attempt(
            actor_user_id=actor_user_id,
            package_id=existing.id,
            source_filename=source_filename,
            source_sha256=source_sha256,
            archive_size_bytes=archive_size_bytes,
            status="duplicate",
            errors=[],
        )
        return _attempt_view(attempt, existing)

    attempt = await unit_of_work.case_packages.add_import_attempt(
        actor_user_id=actor_user_id,
        package_id=model.id,
        source_filename=source_filename,
        source_sha256=source_sha256,
        archive_size_bytes=archive_size_bytes,
        status="accepted",
        errors=[],
    )
    return _attempt_view(attempt, model)


async def record_rejected_case_import(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    source_filename: str,
    source_sha256: str | None,
    archive_size_bytes: int,
    issue: CaseImportIssue,
) -> CaseImportAttemptView:
    """Persist transport-level rejections such as an oversized request body."""
    return await _rejected_attempt(
        unit_of_work,
        actor_user_id,
        source_filename,
        source_sha256,
        archive_size_bytes,
        [issue],
    )


async def list_managed_case_packages(
    unit_of_work: SqlAlchemyUnitOfWork, actor_user_id: int
) -> list[ManagedCasePackageView]:
    organization_ids = await unit_of_work.identity.administrated_organization_ids(actor_user_id)
    if not organization_ids:
        raise CaseAdminError("case_admin_required", "需要组织管理员权限", 403)
    models = await unit_of_work.case_packages.list_managed(actor_user_id, organization_ids)
    return [await _managed_view(unit_of_work, model) for model in models]


async def publish_case_package(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    database_id: int,
    organization_ids: list[str],
) -> ManagedCasePackageView:
    administrated = await unit_of_work.identity.administrated_organization_ids(actor_user_id)
    requested = set(organization_ids)
    if not requested or not requested.issubset(administrated):
        raise CaseAdminError(
            "case_publish_organization_forbidden",
            "只能向当前用户担任管理员的组织发布案件",
            403,
        )
    model = await unit_of_work.case_packages.get_by_database_id(database_id)
    if model is None:
        raise CaseAdminError("case_package_not_found", "案件包不存在", 404)
    existing_organizations = set(await unit_of_work.case_packages.organization_ids(model.id))
    can_manage_existing = bool(existing_organizations & administrated)
    if model.uploaded_by_user_id != actor_user_id and not can_manage_existing:
        raise CaseAdminError("case_package_access_denied", "无权发布该草稿案件", 403)

    for organization_id in requested:
        await unit_of_work.identity.grant_case_access(model.id, organization_id)
    if model.lifecycle_status == "draft":
        model.lifecycle_status = "published"
        model.published_at = datetime.now(UTC)
    return await _managed_view(unit_of_work, model)


async def update_case_package_access(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    database_id: int,
    organization_ids: list[str],
) -> ManagedCasePackageView:
    """精确设置案件包的授权组织列表；发布状态案件也可用于追加或回收组织范围。"""
    administrated = await unit_of_work.identity.administrated_organization_ids(actor_user_id)
    requested = set(organization_ids)
    if not requested or not requested.issubset(administrated):
        raise CaseAdminError(
            "case_publish_organization_forbidden",
            "只能向当前用户担任管理员的组织授权案件",
            403,
        )
    model = await unit_of_work.case_packages.get_by_database_id(database_id)
    if model is None:
        raise CaseAdminError("case_package_not_found", "案件包不存在", 404)
    existing_organizations = set(await unit_of_work.case_packages.organization_ids(model.id))
    can_manage_existing = bool(existing_organizations & administrated)
    if model.uploaded_by_user_id != actor_user_id and not can_manage_existing:
        raise CaseAdminError("case_package_access_denied", "无权修改该案件授权范围", 403)

    for organization_id in requested - existing_organizations:
        await unit_of_work.identity.grant_case_access(model.id, organization_id)
    for organization_id in existing_organizations - requested:
        await unit_of_work.identity.revoke_case_access(model.id, organization_id)
    return await _managed_view(unit_of_work, model)


async def delete_case_package(
    unit_of_work: SqlAlchemyUnitOfWork,
    *,
    actor_user_id: int,
    database_id: int,
) -> None:
    """删除草稿案件包；已发布或已有庭审会话的案件不允许删除。"""
    model = await unit_of_work.case_packages.get_by_database_id(database_id)
    if model is None:
        raise CaseAdminError("case_package_not_found", "案件包不存在", 404)
    if model.lifecycle_status != "draft":
        raise CaseAdminError(
            "case_package_delete_published",
            "只能删除草稿状态的案件包",
            422,
        )
    if model.uploaded_by_user_id != actor_user_id:
        administrated = await unit_of_work.identity.administrated_organization_ids(actor_user_id)
        existing_organizations = set(await unit_of_work.case_packages.organization_ids(model.id))
        if not (existing_organizations & administrated):
            raise CaseAdminError("case_package_access_denied", "无权删除该案件包", 403)

    session_count = await unit_of_work.session.scalar(
        select(func.count(CourtSessionModel.id)).where(CourtSessionModel.package_id == model.id)
    )
    if session_count and session_count > 0:
        raise CaseAdminError(
            "case_package_has_sessions",
            "该案件包已存在庭审会话，无法删除",
            422,
        )

    await unit_of_work.case_packages.delete(database_id)


async def _rejected_attempt(
    unit_of_work: SqlAlchemyUnitOfWork,
    actor_user_id: int,
    source_filename: str,
    source_sha256: str | None,
    archive_size_bytes: int,
    issues: list[CaseImportIssue],
) -> CaseImportAttemptView:
    attempt = await unit_of_work.case_packages.add_import_attempt(
        actor_user_id=actor_user_id,
        package_id=None,
        source_filename=source_filename,
        source_sha256=source_sha256,
        archive_size_bytes=archive_size_bytes,
        status="rejected",
        errors=[item.model_dump(mode="json") for item in issues],
    )
    return _attempt_view(attempt, None)


def _attempt_view(
    attempt: CaseImportAttemptModel, package: CasePackageModel | None
) -> CaseImportAttemptView:
    return CaseImportAttemptView(
        import_id=attempt.id,
        status=cast(Literal["accepted", "rejected", "duplicate"], attempt.status),
        source_filename=attempt.source_filename,
        source_sha256=attempt.source_sha256,
        archive_size_bytes=attempt.archive_size_bytes,
        errors=[CaseImportIssue.model_validate(item) for item in attempt.errors],
        case_id=package.case_id if package else None,
        package_version=package.package_version if package else None,
        database_id=package.id if package else None,
        lifecycle_status=(
            cast(Literal["draft", "published"], package.lifecycle_status) if package else None
        ),
        created_at=attempt.created_at,
    )


async def _managed_view(
    unit_of_work: SqlAlchemyUnitOfWork, model: CasePackageModel
) -> ManagedCasePackageView:
    database_id = model.id
    return ManagedCasePackageView(
        database_id=database_id,
        case_id=model.case_id,
        package_version=model.package_version,
        title=model.title,
        content_status=model.status,
        lifecycle_status=cast(Literal["draft", "published"], model.lifecycle_status),
        jurisdiction=model.jurisdiction,
        law_as_of_date=model.law_as_of_date,
        source_filename=model.source_filename,
        source_sha256=model.source_sha256,
        uploaded_by_user_id=model.uploaded_by_user_id,
        created_at=model.created_at,
        published_at=model.published_at,
        organization_ids=await unit_of_work.case_packages.organization_ids(database_id),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validation_issues(
    error: ValidationError | ValueError, package_root: Path
) -> list[CaseImportIssue]:
    if isinstance(error, ValidationError):
        return [
            CaseImportIssue(
                code="case_package_validation_failed",
                path=".".join(str(part) for part in item["loc"]) or None,
                message=item["msg"],
            )
            for item in error.errors(include_url=False, include_input=False)
        ]
    # 不把服务器临时目录写入审计或返回给客户端。
    message = str(error).replace(str(package_root), ".")
    return [CaseImportIssue(code="case_package_validation_failed", message=message)]
