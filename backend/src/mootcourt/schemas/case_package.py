from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 严格模式
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

# 宽松模式
class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class PackageManifest(StrictModel):
    package_id: str
    package_version: str
    status: str
    created_at: datetime
    law_as_of_date: date
    files: list[str]
    runtime_excluded_files: list[str]
    role_scoped_files: list[str]
    release_hash: str | None
    approved_for_development: bool
    release_blockers: list[str]


class Venue(StrictModel):
    country: str
    province_level_region: str
    fictional_district: str
    assumed_first_instance_court: str


class DisputedIssue(StrictModel):
    id: str
    title: str
    description: str


class CaseRecord(StrictModel):
    id: str
    title: str
    summary: str
    fictional: bool
    jurisdiction: str
    venue: Venue
    case_type: str
    charge_draft: str
    legal_profile_id: str
    law_as_of_date: date
    estimated_duration_minutes: int = Field(gt=0)
    available_user_roles: list[Literal["prosecution", "defense"]]
    defendant_id: str
    witness_ids: list[str]
    disputed_issues: list[DisputedIssue]
    public_material_ids: list[str]
    status: str
    development_mode_allowed: bool
    legal_conclusion_allowed: bool
    disclaimer: str


class FactRecord(StrictModel):
    id: str
    description: str
    status: str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    supporting_statement_ids: list[str]
    materiality: str
    claim_type: str | None = None


class FactsFile(StrictModel):
    case_id: str
    facts: list[FactRecord]


class EvidenceRecord(StrictModel):
    id: str
    type: str
    title: str
    content: str
    source: str
    reliability_notes: list[str]
    available_to: list[Literal["prosecution", "defense"]]
    related_fact_ids: list[str]
    status: str


class EvidenceFile(StrictModel):
    case_id: str
    evidence: list[EvidenceRecord]


class StatementRecord(StrictModel):
    id: str
    text: str
    related_fact_ids: list[str]
    certainty: str


class ParticipantRecord(StrictModel):
    id: str
    name: str
    fictional: bool
    public_profile: str
    forbidden_fact_ids: list[str]
    statements: list[StatementRecord]
    uncertainties: list[str]
    age_at_incident: int | None = None
    known_fact_ids: list[str] | None = None
    allowed_fact_ids: list[str] | None = None
    prior_statement_ids: list[str] | None = None
    statement_ids: list[str] | None = None
    defense_position: str | None = None
    private_background: str | None = None


class DefendantFile(StrictModel):
    case_id: str
    defendant: ParticipantRecord


class WitnessesFile(StrictModel):
    case_id: str
    witnesses: list[ParticipantRecord]


class RoleMaterial(StrictModel):
    id: str
    role: Literal["prosecution", "defense"]
    visibility: list[Literal["prosecution", "defense"]]
    title: str
    objectives: list[str]
    priority_evidence_ids: list[str]
    known_weaknesses: list[str]


class RoleMaterialsFile(StrictModel):
    case_id: str
    materials: list[RoleMaterial]


class LegalSourceRecord(FlexibleModel):
    id: str
    jurisdiction: str
    instrument_title: str
    article_number: str
    text_snapshot: str
    official_source_url: str | None
    source_type: str
    authority_level: str
    status: str
    review_status: str


class LegalSourcesFile(FlexibleModel):
    legal_sources: list[LegalSourceRecord]


class LegalProfile(FlexibleModel):
    id: str
    case_id: str
    jurisdiction: str
    law_as_of_date: date
    substantive_source_ids: list[str]
    procedure_source_ids: list[str]
    evidence_rule_source_ids: list[str]
    development_mode_allowed: bool
    legal_conclusion_allowed: bool


class LegalIssuesFile(FlexibleModel):
    case_id: str
    profile_id: str


class ProcedureProfile(FlexibleModel):
    id: str
    case_id: str
    stages: list[str]


class ReviewManifest(FlexibleModel):
    case_id: str
    package_version: str
    overall_status: str
    approved_for_development: bool
    approved_for_release: bool
    approved_for_legal_conclusion: bool


class CasePackage(StrictModel):
    manifest: PackageManifest
    case: CaseRecord
    facts: FactsFile
    evidence: EvidenceFile
    defendant: DefendantFile
    witnesses: WitnessesFile
    role_materials: RoleMaterialsFile
    legal_profile: LegalProfile
    legal_sources: LegalSourcesFile
    legal_issues: LegalIssuesFile
    procedure_profile: ProcedureProfile
    review_manifest: ReviewManifest

    @model_validator(mode="after")
    def validate_package_links(self) -> CasePackage:
        case_id = self.manifest.package_id
        linked_case_ids = {
            self.case.id,
            self.facts.case_id,
            self.evidence.case_id,
            self.defendant.case_id,
            self.witnesses.case_id,
            self.role_materials.case_id,
            self.legal_profile.case_id,
            self.legal_issues.case_id,
            self.procedure_profile.case_id,
            self.review_manifest.case_id,
        }
        if linked_case_ids != {case_id}:
            raise ValueError("case IDs are inconsistent across package files")
        if not self.case.fictional:
            raise ValueError("only fictional cases may be imported")
        if not self.manifest.approved_for_development:
            raise ValueError("case package is not approved for development")
        if "author_only/ground_truth.json" not in self.manifest.runtime_excluded_files:
            raise ValueError("author-only ground truth must be excluded from runtime")

        fact_ids = {item.id for item in self.facts.facts}
        evidence_ids = {item.id for item in self.evidence.evidence}
        participants = [self.defendant.defendant, *self.witnesses.witnesses]
        statement_ids = {statement.id for item in participants for statement in item.statements}
        source_ids = {item.id for item in self.legal_sources.legal_sources}
        if len(fact_ids) != len(self.facts.facts):
            raise ValueError("duplicate fact IDs")
        if len(evidence_ids) != len(self.evidence.evidence):
            raise ValueError("duplicate evidence IDs")
        if len(statement_ids) != sum(len(item.statements) for item in participants):
            raise ValueError("duplicate statement IDs")

        for fact in self.facts.facts:
            if set(fact.supporting_evidence_ids + fact.contradicting_evidence_ids) - evidence_ids:
                raise ValueError(f"fact {fact.id} references unknown evidence")
            if set(fact.supporting_statement_ids) - statement_ids:
                raise ValueError(f"fact {fact.id} references unknown statements")
        for item in self.evidence.evidence:
            if set(item.related_fact_ids) - fact_ids:
                raise ValueError(f"evidence {item.id} references unknown facts")
        profile_sources = (
            self.legal_profile.substantive_source_ids
            + self.legal_profile.procedure_source_ids
            + self.legal_profile.evidence_rule_source_ids
        )
        if set(profile_sources) - source_ids:
            raise ValueError("legal profile references unknown legal sources")
        return self


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing package file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def load_case_package(package_path: Path) -> CasePackage:
    package_path = package_path.resolve()
    manifest = PackageManifest.model_validate(_read_json(package_path / "manifest.json"))
    forbidden_runtime_files = {
        relative_path
        for relative_path in manifest.runtime_excluded_files
        if relative_path.startswith("author_only/")
    }
    if "author_only/ground_truth.json" not in forbidden_runtime_files:
        raise ValueError("package does not exclude author-only ground truth")

    return CasePackage(
        manifest=manifest,
        case=CaseRecord.model_validate(_read_json(package_path / "case.json")),
        facts=FactsFile.model_validate(_read_json(package_path / "facts.json")),
        evidence=EvidenceFile.model_validate(_read_json(package_path / "evidence.json")),
        defendant=DefendantFile.model_validate(
            _read_json(package_path / "participants" / "defendant.json")
        ),
        witnesses=WitnessesFile.model_validate(
            _read_json(package_path / "participants" / "witnesses.json")
        ),
        role_materials=RoleMaterialsFile.model_validate(
            _read_json(package_path / "role_materials.json")
        ),
        legal_profile=LegalProfile.model_validate(_read_json(package_path / "legal_profile.json")),
        legal_sources=LegalSourcesFile.model_validate(
            _read_json(package_path / "legal_sources.json")
        ),
        legal_issues=LegalIssuesFile.model_validate(_read_json(package_path / "legal_issues.json")),
        procedure_profile=ProcedureProfile.model_validate(
            _read_json(package_path / "procedure_profile.json")
        ),
        review_manifest=ReviewManifest.model_validate(
            _read_json(package_path / "review_manifest.json")
        ),
    )
