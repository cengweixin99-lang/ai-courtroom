from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictLegalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotRequirement(StrictLegalModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9A-Fa-f]{64}$")
    required_for_release: bool
    source_ids: list[str]


class LegalSourceManifest(StrictLegalModel):
    schema_version: str
    dataset_id: str
    index_version: str
    purpose: str
    source_data_path: str
    approved_for_development_retrieval: bool
    legal_conclusion_allowed: bool
    approved_review_statuses: list[str]
    approved_source_ids: list[str]
    snapshot_requirements: list[SnapshotRequirement]
    release_blockers: list[str]

    @model_validator(mode="after")
    def validate_approval_boundary(self) -> LegalSourceManifest:
        if not self.approved_for_development_retrieval:
            raise ValueError("legal source manifest is not approved for development retrieval")
        if self.legal_conclusion_allowed:
            raise ValueError("M3.1 manifest must not enable deterministic legal conclusions")
        if len(self.approved_source_ids) != len(set(self.approved_source_ids)):
            raise ValueError("approved_source_ids contains duplicates")
        return self


class LegalArticleSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    jurisdiction: str
    instrument_title: str
    article_number: str
    text_snapshot: str = Field(min_length=1)
    effective_from: date | None = None
    effective_to: date | None = None
    official_source_url: str | None = None
    issuer: str | None = None
    source_type: str
    authority_level: str
    status: str
    review_status: str
    version_hash: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> LegalArticleSource:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from")
        if self.status == "effective" and self.effective_from is None:
            raise ValueError("effective legal sources require effective_from")
        if self.status == "effective" and self.official_source_url is None:
            raise ValueError("effective legal sources require an official source URL")
        return self


class LegalArticleDocument(StrictLegalModel):
    source_id: str
    dataset_id: str
    index_version: str
    jurisdiction: str
    instrument_title: str
    article_number: str
    text: str
    effective_from: date | None
    effective_to: date | None
    official_source_url: str | None
    issuer: str | None
    source_type: str
    authority_level: str
    status: str
    review_status: str
    version_hash: str | None
    legal_conclusion_allowed: bool
    embedding_version: str | None = None
    embedding: list[float] | None = None


class LegalIndexResult(StrictLegalModel):
    dataset_id: str
    index_name: str
    indexed_count: int
    release_blockers: list[str]
    embedding_version: str | None = None


class LegalRetrievalMode(StrEnum):
    BM25 = "bm25"
    HYBRID_RRF = "hybrid_rrf"


class LegalSearchOutcome(StrEnum):
    SUFFICIENT_LEGAL_AUTHORITY = "SUFFICIENT_LEGAL_AUTHORITY"
    INSUFFICIENT_LEGAL_AUTHORITY = "INSUFFICIENT_LEGAL_AUTHORITY"


class LegalSearchRequest(StrictLegalModel):
    case_id: str = Field(description="用于锁定 LegalProfile 的案件业务标识")
    query: str = Field(min_length=2, max_length=500, description="单一、明确的法律检索问题")
    package_version: str | None = Field(
        default=None,
        description="案件包版本；不传时使用最新导入版本",
    )
    top_k: int = Field(default=5, ge=1, le=20, description="最多返回的候选条款数量")


class LegalSearchHit(StrictLegalModel):
    source_id: str
    instrument_title: str
    article_number: str
    text: str
    jurisdiction: str
    effective_from: date | None
    effective_to: date | None
    status: str
    review_status: str
    authority_level: str
    official_source_url: str | None
    version_hash: str | None
    score: float = Field(description="BM25 原始分或 hybrid 模式下的 RRF 融合分")
    retrieval_mode: LegalRetrievalMode = Field(
        default=LegalRetrievalMode.BM25, description="本条候选的实际召回模式"
    )
    bm25_score: float | None = Field(default=None, description="Elasticsearch BM25 原始分")
    vector_score: float | None = Field(default=None, description="Elasticsearch 向量相似度分")
    bm25_rank: int | None = Field(default=None, ge=1, description="BM25 候选排名")
    vector_rank: int | None = Field(default=None, ge=1, description="向量候选排名")


class LegalSearchResponse(StrictLegalModel):
    trace_id: str = Field(description="本次检索的审计 Trace 标识")
    outcome: LegalSearchOutcome
    legal_profile_id: str
    jurisdiction: str
    law_as_of_date: date
    query: str
    hits: list[LegalSearchHit]
    retrieval_mode: LegalRetrievalMode = Field(description="本次请求实际使用的检索模式")
    embedding_version: str | None = Field(default=None, description="hybrid 模式使用的向量模型版本")
    disclaimer: str = "仅返回审核后的候选法律依据，不构成法律结论或法律意见。"


class LegalSearchFilters(StrictLegalModel):
    jurisdiction: str
    law_as_of_date: date
    allowed_source_ids: list[str]
    approved_review_statuses: list[str]
    top_k: int


class LegalSearchTraceView(StrictLegalModel):
    id: str
    case_id: str
    package_version: str
    legal_profile_id: str
    query: str
    retrieval_mode: LegalRetrievalMode
    embedding_version: str | None
    outcome: LegalSearchOutcome
    filters: LegalSearchFilters
    hits: list[LegalSearchHit]
    latency_ms: int
    created_at: datetime


class LegalCitation(StrictLegalModel):
    source_id: str
    article_number: str
    text: str
    official_source_url: str | None
    version_hash: str | None


class LegalCitationValidationRequest(StrictLegalModel):
    trace_id: str
    citations: list[LegalCitation] = Field(min_length=1, max_length=20)


class LegalCitationFailure(StrEnum):
    SOURCE_NOT_RETRIEVED = "SOURCE_NOT_RETRIEVED"
    ARTICLE_NUMBER_MISMATCH = "ARTICLE_NUMBER_MISMATCH"
    TEXT_MISMATCH = "TEXT_MISMATCH"
    OFFICIAL_SOURCE_MISMATCH = "OFFICIAL_SOURCE_MISMATCH"
    VERSION_HASH_MISMATCH = "VERSION_HASH_MISMATCH"


class LegalCitationValidationItem(StrictLegalModel):
    source_id: str
    valid: bool
    failures: list[LegalCitationFailure]


class LegalCitationValidationResponse(StrictLegalModel):
    trace_id: str
    valid: bool
    results: list[LegalCitationValidationItem]


def load_legal_source_manifest(
    manifest_path: Path,
) -> tuple[LegalSourceManifest, list[LegalArticleDocument]]:
    manifest_path = manifest_path.resolve()
    manifest = LegalSourceManifest.model_validate(_read_object(manifest_path))
    source_path = (manifest_path.parent / manifest.source_data_path).resolve()
    source_root = _read_object(source_path)
    raw_sources = source_root.get("legal_sources")
    if not isinstance(raw_sources, list):
        raise ValueError("legal source data must contain a legal_sources array")
    sources = [LegalArticleSource.model_validate(item) for item in raw_sources]
    by_id = {item.id: item for item in sources}
    if len(by_id) != len(sources):
        raise ValueError("legal source data contains duplicate source IDs")

    missing = set(manifest.approved_source_ids) - set(by_id)
    if missing:
        raise ValueError(f"manifest references unknown source IDs: {sorted(missing)}")
    unapproved_statuses = {
        by_id[source_id].review_status
        for source_id in manifest.approved_source_ids
        if by_id[source_id].review_status not in manifest.approved_review_statuses
    }
    if unapproved_statuses:
        raise ValueError(
            f"manifest contains unapproved review statuses: {sorted(unapproved_statuses)}"
        )

    _validate_snapshots(manifest_path.parent, manifest.snapshot_requirements)
    documents = [
        _article_document(manifest, by_id[source_id]) for source_id in manifest.approved_source_ids
    ]
    return manifest, documents


def _article_document(
    manifest: LegalSourceManifest, source: LegalArticleSource
) -> LegalArticleDocument:
    return LegalArticleDocument(
        source_id=source.id,
        dataset_id=manifest.dataset_id,
        index_version=manifest.index_version,
        jurisdiction=source.jurisdiction,
        instrument_title=source.instrument_title,
        article_number=source.article_number,
        text=source.text_snapshot,
        effective_from=source.effective_from,
        effective_to=source.effective_to,
        official_source_url=source.official_source_url,
        issuer=source.issuer,
        source_type=source.source_type,
        authority_level=source.authority_level,
        status=source.status,
        review_status=source.review_status,
        version_hash=source.version_hash,
        legal_conclusion_allowed=manifest.legal_conclusion_allowed,
    )


def _validate_snapshots(base_path: Path, requirements: list[SnapshotRequirement]) -> None:
    for requirement in requirements:
        snapshot_path = (base_path / requirement.path).resolve()
        if not snapshot_path.exists():
            # 缺失的发布快照是显式 release blocker；开发检索仍使用已审核条款快照。
            continue
        digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        if digest.lower() != requirement.sha256.lower():
            raise ValueError(f"snapshot hash mismatch: {requirement.path}")


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing legal source file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value
