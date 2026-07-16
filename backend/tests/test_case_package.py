from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mootcourt.db.models import CasePackageModel, LegalSourceModel
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import load_case_package
from mootcourt.services.case_importer import import_case_package

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


def test_case_deletion_does_not_cascade_to_audit_sessions() -> None:
    relationship = inspect(CasePackageModel).relationships["sessions"]

    assert "delete" not in relationship.cascade
    assert relationship.passive_deletes is True
