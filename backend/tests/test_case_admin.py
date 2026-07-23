from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.api.dependencies import get_unit_of_work, require_authenticated_principal
from mootcourt.core.auth import AuthenticatedPrincipal
from mootcourt.core.config import Settings, get_settings
from mootcourt.db.models import (
    CaseImportAttemptModel,
    OrganizationMembershipModel,
    PlatformUserModel,
)
from mootcourt.main import app
from mootcourt.repositories.identity import PUBLIC_TRAINING_ORGANIZATION_ID
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.services.case_archive import CaseArchiveError, extract_case_archive
from mootcourt.services.case_importer import import_case_package

CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"
ADMIN_SUBJECT = "case-admin-user"


@pytest_asyncio.fixture
async def case_admin_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()

    async def override_unit_of_work() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with session_factory() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            try:
                yield unit_of_work
                await unit_of_work.commit()
            except Exception:
                await unit_of_work.rollback()
                raise

    app.dependency_overrides[get_unit_of_work] = override_unit_of_work
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject=ADMIN_SUBJECT,
        email="case-admin@example.test",
        provider_role="authenticated",
        claims={"sub": ADMIN_SUBJECT},
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 首次请求创建本地用户，再由测试数据库明确授予组织管理员角色。
        assert (await client.get("/api/v1/cases")).status_code == 200
        async with session_factory() as session:
            user_id = await session.scalar(
                select(PlatformUserModel.id).where(PlatformUserModel.auth_subject == ADMIN_SUBJECT)
            )
            assert user_id is not None
            membership = await session.scalar(
                select(OrganizationMembershipModel).where(
                    OrganizationMembershipModel.organization_id == PUBLIC_TRAINING_ORGANIZATION_ID,
                    OrganizationMembershipModel.user_id == user_id,
                )
            )
            assert membership is not None
            membership.role = "admin"
            await session.commit()
        yield client
    app.dependency_overrides.clear()


async def test_admin_imports_draft_then_publishes_to_organization(
    case_admin_client: AsyncClient,
) -> None:
    archive = _package_archive(package_version="9.9.0-admin-test")
    organizations = await case_admin_client.get("/api/v1/admin/case-packages/organizations")

    imported = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=archive,
        headers={"Content-Type": "application/zip", "X-Filename": "case-001.zip"},
    )
    cases_before = await case_admin_client.get("/api/v1/cases")
    blocked_session = await case_admin_client.post(
        "/api/v1/sessions",
        json={
            "case_id": "CASE-001",
            "package_version": "9.9.0-admin-test",
            "user_role": "prosecution",
        },
    )

    assert organizations.status_code == 200
    assert organizations.json() == [
        {
            "id": PUBLIC_TRAINING_ORGANIZATION_ID,
            "slug": "public-training",
            "name": "Public Training",
        }
    ]
    assert imported.status_code == 201
    body = imported.json()
    assert body["status"] == "accepted"
    assert body["lifecycle_status"] == "draft"
    assert {item["package_version"] for item in cases_before.json()} == {"0.2.0-dev"}
    assert blocked_session.status_code == 404

    published = await case_admin_client.post(
        f"/api/v1/admin/case-packages/{body['database_id']}/publish",
        json={"organization_ids": [PUBLIC_TRAINING_ORGANIZATION_ID]},
    )
    cases_after = await case_admin_client.get("/api/v1/cases")
    started = await case_admin_client.post(
        "/api/v1/sessions",
        json={
            "case_id": "CASE-001",
            "package_version": "9.9.0-admin-test",
            "user_role": "prosecution",
        },
    )

    assert published.status_code == 200
    assert published.json()["lifecycle_status"] == "published"
    assert published.json()["organization_ids"] == [PUBLIC_TRAINING_ORGANIZATION_ID]
    assert {item["package_version"] for item in cases_after.json()} == {
        "0.2.0-dev",
        "9.9.0-admin-test",
    }
    assert started.status_code == 201


async def test_unsafe_archive_is_rejected_and_audited(
    case_admin_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    archive = io.BytesIO()
    with ZipFile(archive, "w", compression=ZIP_STORED) as output:
        output.writestr("../manifest.json", "{}")

    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=archive.getvalue(),
        headers={"Content-Type": "application/zip", "X-Filename": "unsafe.zip"},
    )

    assert response.status_code == 422
    assert response.json()["status"] == "rejected"
    assert response.json()["errors"][0]["code"] == "case_archive_unsafe_path"
    async with session_factory() as session:
        attempts = await session.scalar(
            select(func.count())
            .select_from(CaseImportAttemptModel)
            .where(CaseImportAttemptModel.status == "rejected")
        )
    assert attempts == 1


async def test_archive_cannot_smuggle_an_undeclared_file(
    case_admin_client: AsyncClient,
) -> None:
    archive = io.BytesIO(_package_archive(package_version="9.9.1-smuggled-file"))
    rewritten = io.BytesIO()
    with ZipFile(archive) as source, ZipFile(rewritten, "w", compression=ZIP_STORED) as output:
        for item in source.infolist():
            output.writestr(item, source.read(item))
        output.writestr("hidden.json", "{}")

    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=rewritten.getvalue(),
        headers={"Content-Type": "application/zip", "X-Filename": "smuggled.zip"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0] == {
        "code": "case_archive_undeclared_file",
        "message": "压缩包包含 manifest.files 未声明的文件",
        "path": "hidden.json",
    }


async def test_same_version_with_different_content_is_rejected(
    case_admin_client: AsyncClient,
) -> None:
    first = _package_archive(package_version="9.9.2-immutable")
    changed = io.BytesIO()
    with (
        ZipFile(io.BytesIO(first)) as source,
        ZipFile(changed, "w", compression=ZIP_STORED) as output,
    ):
        for item in source.infolist():
            content = source.read(item)
            if item.filename == "README.md":
                content += b"changed"
            output.writestr(item, content)

    accepted = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=first,
        headers={"Content-Type": "application/zip", "X-Filename": "first.zip"},
    )
    conflict = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=changed.getvalue(),
        headers={"Content-Type": "application/zip", "X-Filename": "changed.zip"},
    )

    assert accepted.status_code == 201
    assert conflict.status_code == 422
    assert conflict.json()["errors"][0]["code"] == "case_package_version_conflict"


async def test_same_archive_import_is_idempotent_and_audited(
    case_admin_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    archive = _package_archive(package_version="9.9.3-idempotent")

    accepted = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=archive,
        headers={"Content-Type": "application/zip", "X-Filename": "first.zip"},
    )
    duplicate = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=archive,
        headers={"Content-Type": "application/zip", "X-Filename": "retry.zip"},
    )

    assert accepted.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["database_id"] == accepted.json()["database_id"]
    async with session_factory() as session:
        attempts = await session.scalar(
            select(func.count())
            .select_from(CaseImportAttemptModel)
            .where(CaseImportAttemptModel.package_id == accepted.json()["database_id"])
        )
    assert attempts == 2


async def test_non_admin_cannot_upload_case_archive(
    case_admin_client: AsyncClient,
) -> None:
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="plain-case-user",
        email="plain@example.test",
        provider_role="authenticated",
        claims={"sub": "plain-case-user"},
    )

    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=b"not-even-read",
        headers={"Content-Type": "application/zip", "X-Filename": "case.zip"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "case_admin_required"


async def test_invalid_content_type_is_rejected_without_a_fake_hash(
    case_admin_client: AsyncClient,
) -> None:
    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=b"not-a-zip",
        headers={"Content-Type": "text/plain", "X-Filename": "case.zip"},
    )

    assert response.status_code == 415
    assert response.json()["status"] == "rejected"
    assert response.json()["source_sha256"] is None
    assert response.json()["errors"][0]["code"] == "case_archive_content_type_invalid"


async def test_archive_filename_cannot_contain_a_path(case_admin_client: AsyncClient) -> None:
    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=b"unused",
        headers={"Content-Type": "application/zip", "X-Filename": "../case.zip"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "case_archive_filename_invalid"
    assert response.json()["source_sha256"] is None


async def test_archive_request_body_limit_is_enforced(case_admin_client: AsyncClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(case_import_max_archive_bytes=1024)

    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/imports",
        content=b"x" * 1025,
        headers={"Content-Type": "application/zip", "X-Filename": "large.zip"},
    )

    assert response.status_code == 413
    assert response.json()["archive_size_bytes"] == 1025
    assert response.json()["source_sha256"] is None
    assert response.json()["errors"][0]["code"] == "case_archive_too_large"


async def test_non_admin_cannot_list_managed_packages(case_admin_client: AsyncClient) -> None:
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="plain-list-user",
        email="plain-list@example.test",
        provider_role="authenticated",
        claims={"sub": "plain-list-user"},
    )

    response = await case_admin_client.get("/api/v1/admin/case-packages")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "case_admin_required"


async def test_publish_unknown_package_returns_stable_error(case_admin_client: AsyncClient) -> None:
    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/999999/publish",
        json={"organization_ids": [PUBLIC_TRAINING_ORGANIZATION_ID]},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "case_package_not_found"


async def test_publish_rejects_organization_outside_admin_scope(
    case_admin_client: AsyncClient,
) -> None:
    response = await case_admin_client.post(
        "/api/v1/admin/case-packages/1/publish",
        json={"organization_ids": ["00000000-0000-0000-0000-000000000099"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "case_publish_organization_forbidden"


async def test_organization_admin_can_manage_members_without_removing_last_admin(
    case_admin_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        admin_id = await session.scalar(
            select(PlatformUserModel.id).where(PlatformUserModel.auth_subject == ADMIN_SUBJECT)
        )
        session.add(
            PlatformUserModel(
                auth_subject="managed-member",
                email="member@example.test",
                display_name="测试成员",
            )
        )
        await session.commit()
        member_id = await session.scalar(
            select(PlatformUserModel.id).where(PlatformUserModel.auth_subject == "managed-member")
        )

    listed = await case_admin_client.get(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members"
    )
    added = await case_admin_client.put(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members/{member_id}",
        json={"role": "learner"},
    )
    blocked_demotion = await case_admin_client.put(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members/{admin_id}",
        json={"role": "learner"},
    )
    promoted = await case_admin_client.put(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members/{member_id}",
        json={"role": "admin"},
    )
    removed_admin = await case_admin_client.delete(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members/{admin_id}"
    )
    blocked_last_removal = await case_admin_client.delete(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members/{member_id}"
    )

    assert listed.status_code == 200
    assert any(
        item["user_id"] == admin_id and item["role"] == "admin" for item in listed.json()["members"]
    )
    assert added.status_code == 200
    assert any(
        item["user_id"] == member_id and item["role"] == "learner"
        for item in added.json()["members"]
    )
    assert blocked_demotion.status_code == 409
    assert blocked_demotion.json()["detail"]["code"] == "organization_self_role_change_forbidden"
    assert promoted.status_code == 200
    assert removed_admin.status_code == 409
    assert removed_admin.json()["detail"]["code"] == "organization_self_removal_forbidden"
    assert blocked_last_removal.status_code == 200


async def test_non_admin_cannot_manage_organization_members(
    case_admin_client: AsyncClient,
) -> None:
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        subject="member-manager-denied",
        email="denied@example.test",
        provider_role="authenticated",
        claims={"sub": "member-manager-denied"},
    )

    response = await case_admin_client.get(
        f"/api/v1/admin/organizations/{PUBLIC_TRAINING_ORGANIZATION_ID}/members"
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "organization_admin_required"


@pytest.mark.parametrize(
    ("entries", "expected_code"),
    [
        ([], "case_archive_empty"),
        ([("manifest.json", b"{}"), ("payload.exe", b"x")], "case_archive_file_type_forbidden"),
        ([("A.json", b"{}"), ("a.JSON", b"{}")], "case_archive_duplicate_path"),
    ],
)
def test_archive_structure_rejections(
    tmp_path: Path, entries: list[tuple[str, bytes]], expected_code: str
) -> None:
    archive_path = tmp_path / "case.zip"
    with ZipFile(archive_path, "w", compression=ZIP_STORED) as archive:
        for filename, content in entries:
            archive.writestr(filename, content)

    with pytest.raises(CaseArchiveError) as raised:
        extract_case_archive(
            archive_path,
            tmp_path / "output",
            max_files=20,
            max_uncompressed_bytes=1024 * 1024,
            max_compression_ratio=100,
        )

    assert raised.value.issue.code == expected_code


def test_archive_rejects_suspicious_compression_ratio(tmp_path: Path) -> None:
    archive_path = tmp_path / "compressed.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", b"0" * 4096)

    with pytest.raises(CaseArchiveError) as raised:
        extract_case_archive(
            archive_path,
            tmp_path / "output",
            max_files=20,
            max_uncompressed_bytes=1024 * 1024,
            max_compression_ratio=2,
        )

    assert raised.value.issue.code == "case_archive_suspicious_compression"


def _package_archive(*, package_version: str) -> bytes:
    archive = io.BytesIO()
    with ZipFile(archive, "w", compression=ZIP_STORED) as output:
        for source in CASE_PACKAGE.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(CASE_PACKAGE).as_posix()
            content = source.read_bytes()
            if relative == "manifest.json":
                manifest = json.loads(content)
                manifest["package_version"] = package_version
                content = json.dumps(manifest, ensure_ascii=False).encode()
            output.writestr(relative, content)
    return archive.getvalue()
