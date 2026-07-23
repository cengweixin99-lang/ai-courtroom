from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mootcourt.db.models import (
    CaseAccessGrantModel,
    CaseImportAttemptModel,
    CasePackageModel,
    EvidenceModel,
    FactModel,
    LegalSourceModel,
    ParticipantModel,
    RoleMaterialModel,
)
from mootcourt.schemas.case_package import CasePackage

CasePackageRecord = CasePackageModel


class SqlAlchemyCasePackageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # 查询已导入的案件包
    async def find_imported(self, case_id: str, package_version: str) -> CasePackageModel | None:
        result: CasePackageModel | None = await self._session.scalar(
            select(CasePackageModel).where(
                CasePackageModel.case_id == case_id,
                CasePackageModel.package_version == package_version,
            )
        )
        return result

    # 添加案件包
    async def add(
        self,
        package: CasePackage,
        *,
        lifecycle_status: str = "published",
        source_filename: str | None = None,
        source_sha256: str | None = None,
        uploaded_by_user_id: int | None = None,
    ) -> CasePackageModel:
        model = _build_package_model(package)
        model.lifecycle_status = lifecycle_status
        model.source_filename = source_filename
        model.source_sha256 = source_sha256
        model.uploaded_by_user_id = uploaded_by_user_id
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model

    async def add_if_absent(
        self,
        package: CasePackage,
        *,
        lifecycle_status: str = "published",
        source_filename: str | None = None,
        source_sha256: str | None = None,
        uploaded_by_user_id: int | None = None,
    ) -> tuple[CasePackageModel, bool]:
        """以数据库唯一键为最终仲裁，幂等创建案件包。

        先查再插入只能减少常规重复请求，无法消除并发竞态。唯一键冲突时回滚
        当前导入事务，再开启一个干净事务读取胜者，避免把数据库异常暴露给 API。
        """
        existing = await self.find_imported(
            package.manifest.package_id, package.manifest.package_version
        )
        if existing is not None:
            return existing, False

        try:
            model = await self.add(
                package,
                lifecycle_status=lifecycle_status,
                source_filename=source_filename,
                source_sha256=source_sha256,
                uploaded_by_user_id=uploaded_by_user_id,
            )
        except IntegrityError:
            # 另一个事务可能刚刚提交了同一案件版本；重新读取并按重复请求处理。
            await self._session.rollback()
            existing = await self.find_imported(
                package.manifest.package_id, package.manifest.package_version
            )
            if existing is None:
                raise
            return existing, False
        return model, True

    # 列出所有案件包（预加载关联数据）
    async def list_all(self) -> list[CasePackageModel]:
        return list(
            await self._session.scalars(
                select(CasePackageModel).order_by(
                    CasePackageModel.case_id, CasePackageModel.id.desc()
                )
            )
        )

    async def list_managed(
        self, user_id: int, administrated_organization_ids: set[str]
    ) -> list[CasePackageModel]:
        statement = select(CasePackageModel).order_by(
            CasePackageModel.case_id, CasePackageModel.id.desc()
        )
        access = CasePackageModel.uploaded_by_user_id == user_id
        if administrated_organization_ids:
            access = or_(
                access,
                CasePackageModel.id.in_(
                    select(CaseAccessGrantModel.package_id).where(
                        CaseAccessGrantModel.organization_id.in_(administrated_organization_ids)
                    )
                ),
            )
        return list(await self._session.scalars(statement.where(access)))

    async def organization_ids(self, package_id: int) -> list[str]:
        return list(
            await self._session.scalars(
                select(CaseAccessGrantModel.organization_id)
                .where(CaseAccessGrantModel.package_id == package_id)
                .order_by(CaseAccessGrantModel.organization_id)
            )
        )

    async def add_import_attempt(
        self,
        *,
        actor_user_id: int,
        package_id: int | None,
        source_filename: str,
        source_sha256: str | None,
        archive_size_bytes: int,
        status: str,
        errors: list[dict[str, Any]],
    ) -> CaseImportAttemptModel:
        attempt = CaseImportAttemptModel(
            actor_user_id=actor_user_id,
            package_id=package_id,
            source_filename=source_filename,
            source_sha256=source_sha256,
            archive_size_bytes=archive_size_bytes,
            status=status,
            errors=errors,
        )
        self._session.add(attempt)
        await self._session.flush()
        await self._session.refresh(attempt)
        return attempt

    # 获取运行时案件包
    async def get_runtime_package(
        self, case_id: str, package_version: str | None = None
    ) -> CasePackageModel | None:
        statement = (
            select(CasePackageModel)
            .where(
                CasePackageModel.case_id == case_id,
                CasePackageModel.lifecycle_status == "published",
            )
            .options(
                selectinload(CasePackageModel.facts),
                selectinload(CasePackageModel.evidence),
                selectinload(CasePackageModel.participants),
                selectinload(CasePackageModel.role_materials),
                selectinload(CasePackageModel.legal_sources),
            )
            .order_by(CasePackageModel.id.desc())
        )
        if package_version is not None:
            statement = statement.where(CasePackageModel.package_version == package_version)
        result: CasePackageModel | None = await self._session.scalar(statement.limit(1))
        return result

    # 按数据库 ID 查询
    async def get_by_database_id(self, database_id: int) -> CasePackageModel | None:
        return await self._session.get(CasePackageModel, database_id)

    async def get_runtime_package_by_database_id(self, database_id: int) -> CasePackageModel | None:
        """按会话锁定的数据库 ID 加载构造运行时上下文所需的全部关联数据。"""
        result: CasePackageModel | None = await self._session.scalar(
            select(CasePackageModel)
            .where(CasePackageModel.id == database_id)
            .options(
                selectinload(CasePackageModel.facts),
                selectinload(CasePackageModel.evidence),
                selectinload(CasePackageModel.participants),
                selectinload(CasePackageModel.role_materials),
                selectinload(CasePackageModel.legal_sources),
            )
        )
        return result


def _build_package_model(package: CasePackage) -> CasePackageModel:
    def dump(model: BaseModel) -> dict[str, Any]:
        return model.model_dump(mode="json", exclude_none=True)

    model = CasePackageModel(
        case_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        status=package.manifest.status,
        title=package.case.title,
        jurisdiction=package.case.jurisdiction,
        law_as_of_date=package.manifest.law_as_of_date,
        manifest=dump(package.manifest),
        case_data=dump(package.case),
        legal_profile=dump(package.legal_profile),
        legal_issues=dump(package.legal_issues),
        procedure_profile=dump(package.procedure_profile),
        review_manifest=dump(package.review_manifest),
    )
    model.facts = [
        FactModel(
            fact_id=item.id,
            description=item.description,
            status=item.status,
            payload=dump(item),
        )
        for item in package.facts.facts
    ]
    model.evidence = [
        EvidenceModel(
            evidence_id=item.id,
            evidence_type=item.type,
            title=item.title,
            status=item.status,
            available_to=list(item.available_to),
            payload=dump(item),
        )
        for item in package.evidence.evidence
    ]
    model.participants = [
        ParticipantModel(
            participant_id=package.defendant.defendant.id,
            participant_type="defendant",
            name=package.defendant.defendant.name,
            public_profile=package.defendant.defendant.public_profile,
            payload=dump(package.defendant.defendant),
        ),
        *[
            ParticipantModel(
                participant_id=item.id,
                participant_type="witness",
                name=item.name,
                public_profile=item.public_profile,
                payload=dump(item),
            )
            for item in package.witnesses.witnesses
        ],
    ]
    model.role_materials = [
        RoleMaterialModel(
            material_id=item.id,
            role=item.role,
            title=item.title,
            payload=dump(item),
        )
        for item in package.role_materials.materials
    ]
    model.legal_sources = [
        LegalSourceModel(
            source_id=item.id,
            instrument_title=item.instrument_title,
            article_number=item.article_number,
            status=item.status,
            payload=dump(item),
        )
        for item in package.legal_sources.legal_sources
    ]
    return model
