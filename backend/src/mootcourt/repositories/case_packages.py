from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mootcourt.db.models import (
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
        return await self._session.scalar(
            select(CasePackageModel).where(
                CasePackageModel.case_id == case_id,
                CasePackageModel.package_version == package_version,
            )
        )

    # 添加案件包
    async def add(self, package: CasePackage) -> CasePackageModel:
        model = _build_package_model(package)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return model

    # 列出所有案件包（预加载关联数据）
    async def list_all(self) -> list[CasePackageModel]:
        return list(
            await self._session.scalars(
                select(CasePackageModel).order_by(
                    CasePackageModel.case_id, CasePackageModel.id.desc()
                )
            )
        )

    # 获取运行时案件包
    async def get_runtime_package(
        self, case_id: str, package_version: str | None = None
    ) -> CasePackageModel | None:
        statement = (
            select(CasePackageModel)
            .where(CasePackageModel.case_id == case_id)
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
        return await self._session.scalar(statement.limit(1))

    # 按数据库 ID 查询
    async def get_by_database_id(self, database_id: int) -> CasePackageModel | None:
        return await self._session.get(CasePackageModel, database_id)

    async def get_runtime_package_by_database_id(self, database_id: int) -> CasePackageModel | None:
        """按会话锁定的数据库 ID 加载构造运行时上下文所需的全部关联数据。"""
        return await self._session.scalar(
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
