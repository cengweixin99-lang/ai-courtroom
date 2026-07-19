from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewStatementResolution(StrEnum):
    INCLUDED_IN_RECORD = "INCLUDED_IN_RECORD"
    EXCLUDED_FROM_RECORD = "EXCLUDED_FROM_RECORD"


class NewStatementResolutionRequest(StrictReviewModel):
    resolution: NewStatementResolution = Field(description="是否纳入本庭陈述记录")
    reason: str = Field(min_length=1, max_length=4_000, description="教学控制者审核理由")


class NewStatementResolutionResponse(StrictReviewModel):
    trace_id: str
    resolution: NewStatementResolution
    reason: str
    review_event_sequence: int


class FactFindingStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    DISPUTED = "DISPUTED"
    INSUFFICIENT = "INSUFFICIENT"


class ElementFindingStatus(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    DISPUTED = "DISPUTED"
    INSUFFICIENT = "INSUFFICIENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CourtReviewGenerateRequest(StrictReviewModel):
    legal_search_trace_ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description="覆盖全部冻结构成要件法源的本案法律检索 Trace",
    )


class ReviewLegalIssueConfig(StrictReviewModel):
    id: str
    title: str
    element_ids: list[str]
    related_fact_ids: list[str]
    legal_source_ids: list[str]


class ReviewLegalElementConfig(StrictReviewModel):
    id: str
    text: str
    status: str
    supporting_fact_ids: list[str]
    contradicting_fact_ids: list[str]
    legal_source_ids: list[str]
    proof_status: str


class ReviewLegalIssuesConfig(StrictReviewModel):
    case_id: str
    profile_id: str
    review_status: str
    disputed_issue_ids: list[str]
    legal_issues: list[ReviewLegalIssueConfig]
    elements: list[ReviewLegalElementConfig]
    deterministic_conclusion_allowed: bool
    blocked_reason: str


class ReviewLegalCitation(StrictReviewModel):
    source_id: str
    instrument_title: str
    article_number: str
    text: str
    official_source_url: str | None
    version_hash: str | None
    trace_id: str


class ReviewFactFinding(StrictReviewModel):
    fact_id: str
    description: str
    status: FactFindingStatus
    submitted_supporting_evidence_ids: list[str]
    submitted_contradicting_evidence_ids: list[str]
    appeared_statement_ids: list[str]
    challenged_evidence_ids: list[str]


class ReviewElementFinding(StrictReviewModel):
    element_id: str
    description: str
    status: ElementFindingStatus
    supporting_fact_ids: list[str]
    contradicting_fact_ids: list[str]
    legal_source_ids: list[str]
    citations: list[ReviewLegalCitation]


class CourtReviewReport(StrictReviewModel):
    id: str
    session_id: str
    case_id: str
    package_version: str
    jurisdiction: str
    law_as_of_date: date
    burden_of_proof: str
    standard_of_proof: str
    fact_findings: list[ReviewFactFinding]
    element_findings: list[ReviewElementFinding]
    unresolved_issue_ids: list[str]
    deterministic_conclusion_allowed: bool
    conclusion: str | None
    disclaimer: str
    legal_search_trace_ids: list[str]
    event_sequence_number: int
    created_at: datetime
