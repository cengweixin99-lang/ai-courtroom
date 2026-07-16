from __future__ import annotations

from mootcourt.repositories.case_packages import CasePackageRecord
from mootcourt.repositories.unit_of_work import SqlAlchemyUnitOfWork
from mootcourt.schemas.case_package import (
    CaseRecord,
    EvidenceRecord,
    FactRecord,
    LegalProfile,
    LegalSourceRecord,
    ProcedureProfile,
    RoleMaterial,
    StatementRecord,
)
from mootcourt.schemas.runtime import (
    CaseSummary,
    CaseView,
    ParticipantType,
    ParticipantView,
    UserRole,
)


async def list_case_packages(unit_of_work: SqlAlchemyUnitOfWork) -> list[CaseSummary]:
    rows = await unit_of_work.case_packages.list_all()
    return [
        CaseSummary(
            case_id=row.case_id,
            package_version=row.package_version,
            title=row.title,
            status=row.status,
            jurisdiction=row.jurisdiction,
            law_as_of_date=row.law_as_of_date,
        )
        for row in rows
    ]


async def get_case_package_model(
    unit_of_work: SqlAlchemyUnitOfWork,
    case_id: str,
    package_version: str | None = None,
) -> CasePackageRecord | None:
    return await unit_of_work.case_packages.get_runtime_package(case_id, package_version)


async def build_case_view(
    unit_of_work: SqlAlchemyUnitOfWork,
    case_id: str,
    role: UserRole,
    package_version: str | None = None,
) -> CaseView | None:
    package = await get_case_package_model(unit_of_work, case_id, package_version)
    if package is None:
        return None

    evidence = [
        EvidenceRecord.model_validate(item.payload)
        for item in package.evidence
        if role.value in item.available_to
    ]
    participants = [
        ParticipantView(
            id=item.participant_id,
            participant_type=ParticipantType(item.participant_type),
            name=item.name,
            public_profile=item.public_profile,
            statements=[
                StatementRecord.model_validate(statement)
                for statement in item.payload.get("statements", [])
            ],
        )
        for item in package.participants
    ]
    role_materials = [
        RoleMaterial.model_validate(item.payload)
        for item in package.role_materials
        if item.role == role.value
    ]
    return CaseView(
        case_id=package.case_id,
        package_version=package.package_version,
        role=role,
        case=CaseRecord.model_validate(package.case_data),
        facts=[FactRecord.model_validate(item.payload) for item in package.facts],
        evidence=evidence,
        participants=participants,
        role_materials=role_materials,
        legal_profile=LegalProfile.model_validate(package.legal_profile),
        legal_sources=[
            LegalSourceRecord.model_validate(item.payload)
            for item in package.legal_sources
            if item.status == "effective"
        ],
        procedure_profile=ProcedureProfile.model_validate(package.procedure_profile),
    )
