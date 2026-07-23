from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.db.models import CasePackageModel, LegalSourceModel
from mootcourt.repositories.case_packages import SqlAlchemyCasePackageRepository
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import load_case_package
from mootcourt.schemas.runtime import UserRole
from mootcourt.services.case_importer import import_case_package
from mootcourt.services.case_visibility import build_case_view, list_case_packages


async def test_add_if_absent_recovers_from_concurrent_unique_key_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    package = load_case_package(CASE_PACKAGE)
    concurrent_winner = CasePackageModel(
        id=91,
        case_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
    )
    async with session_factory() as session:
        repository = SqlAlchemyCasePackageRepository(session)
        repository.find_imported = AsyncMock(side_effect=[None, concurrent_winner])  # type: ignore[method-assign]
        repository.add = AsyncMock(  # type: ignore[method-assign]
            side_effect=IntegrityError("INSERT", {}, Exception("duplicate key"))
        )

        model, created = await repository.add_if_absent(package)

    assert model is concurrent_winner
    assert created is False


CASE_PACKAGE = Path(__file__).parents[2] / "data" / "authoring" / "CASE-001"


def test_case_package_schema_validates_cross_file_links() -> None:
    package = load_case_package(CASE_PACKAGE)

    assert package.case.id == "CASE-001"
    assert len(package.facts.facts) == 15
    assert len(package.evidence.evidence) == 11
    assert len(package.legal_sources.legal_sources) == 10
    serialized = package.model_dump_json()
    assert "AUTHOR_ONLY_NEVER_LOAD_AT_RUNTIME" not in serialized
    assert "actual_actor" not in serialized


async def test_case_import_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        first = await import_case_package(unit_of_work, CASE_PACKAGE)
        await unit_of_work.commit()
        second = await import_case_package(unit_of_work, CASE_PACKAGE)
        package_count = await session.scalar(select(func.count()).select_from(CasePackageModel))
        source_count = await session.scalar(select(func.count()).select_from(LegalSourceModel))

    assert first.created is True
    assert second.created is False
    assert first.database_id == second.database_id
    assert package_count == 1
    assert source_count == 10


async def test_import_service_leaves_commit_to_transaction_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await import_case_package(SqlAlchemyUnitOfWork(session), CASE_PACKAGE)

    async with session_factory() as session:
        package_count = await session.scalar(select(func.count()).select_from(CasePackageModel))

    assert package_count == 0


async def test_case_view_filters_private_materials_by_selected_role(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        await import_case_package(unit_of_work, CASE_PACKAGE)

        prosecution = await build_case_view(unit_of_work, "CASE-001", UserRole.PROSECUTION)
        missing = await build_case_view(unit_of_work, "MISSING", UserRole.PROSECUTION)

    assert prosecution is not None
    assert prosecution.role is UserRole.PROSECUTION
    assert prosecution.evidence
    assert all("prosecution" in item.available_to for item in prosecution.evidence)
    assert prosecution.role_materials
    assert all(item.role == "prosecution" for item in prosecution.role_materials)
    assert prosecution.legal_sources
    assert all(item.status == "effective" for item in prosecution.legal_sources)
    assert missing is None


async def test_case_list_applies_access_filter_and_hides_drafts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        imported = await import_case_package(unit_of_work, CASE_PACKAGE)
        visible = await list_case_packages(
            unit_of_work,
            accessible_package_ids={imported.database_id},
        )
        hidden = await list_case_packages(unit_of_work, accessible_package_ids=set())

    assert [item.case_id for item in visible] == ["CASE-001"]
    assert hidden == []


def test_case_deletion_does_not_cascade_to_audit_sessions() -> None:
    relationship = inspect(CasePackageModel).relationships["sessions"]

    assert "delete" not in relationship.cascade
    assert relationship.passive_deletes is True
