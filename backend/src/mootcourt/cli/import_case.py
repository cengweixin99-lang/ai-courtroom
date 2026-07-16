from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from mootcourt.db.session import get_session_factory
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import load_case_package
from mootcourt.services.case_importer import find_imported_package, import_case_package


async def _run(package_path: Path) -> None:
    async with get_session_factory()() as session:
        unit_of_work = SqlAlchemyUnitOfWork(session)
        try:
            result = await import_case_package(unit_of_work, package_path)
            await unit_of_work.commit()
        except IntegrityError:
            await unit_of_work.rollback()
            package = load_case_package(package_path)
            existing = await find_imported_package(
                unit_of_work,
                package.manifest.package_id,
                package.manifest.package_version,
            )
            if existing is None:
                raise
            result = existing
        except Exception:
            await unit_of_work.rollback()
            raise
    print(result.model_dump_json())


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a validated MootCourt case package")
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args.package))


if __name__ == "__main__":
    main()
