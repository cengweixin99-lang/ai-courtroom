from __future__ import annotations

from pathlib import Path

from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import load_case_package
from mootcourt.schemas.runtime import ImportResult


async def import_case_package(
    unit_of_work: SqlAlchemyUnitOfWork, package_path: Path
) -> ImportResult:
    package = load_case_package(package_path)
    # 初始化导入和管理端上传共用数据库级幂等写入，避免多实例启动时相互冲突。
    model, created = await unit_of_work.case_packages.add_if_absent(package)
    await unit_of_work.identity.grant_public_case_access(model.id)
    return ImportResult(
        case_id=model.case_id,
        package_version=model.package_version,
        database_id=model.id,
        created=created,
    )


async def find_imported_package(
    unit_of_work: SqlAlchemyUnitOfWork, case_id: str, package_version: str
) -> ImportResult | None:
    existing = await unit_of_work.case_packages.find_imported(case_id, package_version)
    if existing is None:
        return None
    return ImportResult(
        case_id=existing.case_id,
        package_version=existing.package_version,
        database_id=existing.id,
        created=False,
    )
