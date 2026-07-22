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


class ReviewScoreDimension(StrictReviewModel):
    """一项可追溯的教学评分维度，分值固定为 0 至 100。"""

    key: str
    label: str
    score: int = Field(ge=0, le=100)
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    summary: str


class ReviewRecommendation(StrictReviewModel):
    """由确定性评分规则产出的改进建议，不依赖模型自由生成。"""

    id: str
    priority: str
    title: str
    detail: str
    related_evidence_ids: list[str] = Field(default_factory=list)
    related_fact_ids: list[str] = Field(default_factory=list)
    related_element_ids: list[str] = Field(default_factory=list)


class ReviewTurnCheck(StrictReviewModel):
    """一项基于结构化事件字段的逐发言检查。"""

    key: str
    label: str
    passed: bool
    detail: str


class ReviewTurnDiagnostic(StrictReviewModel):
    """定位到庭审事件序号的确定性诊断，不评价自由文本语义。"""

    event_sequence_number: int = Field(ge=1)
    actor_role: str
    phase: str
    action: str
    score: int = Field(ge=0, le=100)
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    checks: list[ReviewTurnCheck]
    recommendation: str | None = None


class TurnQualityEvaluation(StrictReviewModel):
    """模型对单次用户发言的教学点评，不参与确定性总分计算。"""

    event_sequence_number: int = Field(ge=1)
    organization_score: int = Field(ge=0, le=100)
    responsiveness_score: int = Field(ge=0, le=100)
    advocacy_score: int = Field(ge=0, le=100)
    strengths: list[str] = Field(default_factory=list, max_length=3)
    improvements: list[str] = Field(default_factory=list, max_length=3)
    rewritten_example: str | None = Field(default=None, min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)


class TurnQualityEvaluationBatch(StrictReviewModel):
    evaluations: list[TurnQualityEvaluation] = Field(min_length=1, max_length=10)


class TurnQualityEvaluationGenerateRequest(StrictReviewModel):
    event_sequence_numbers: list[int] = Field(default_factory=list, max_length=10)


class TurnQualityEvaluationReport(StrictReviewModel):
    id: str
    review_id: str
    session_id: str
    provider: str
    model: str
    evaluations: list[TurnQualityEvaluation]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_cny: float = Field(ge=0)
    repair_count: int = Field(ge=0)
    created_at: datetime


class CourtReviewReport(StrictReviewModel):
    id: str
    session_id: str
    case_id: str
    package_version: str
    jurisdiction: str
    law_as_of_date: date
    burden_of_proof: str
    standard_of_proof: str
    user_role: str = ""
    fact_findings: list[ReviewFactFinding]
    element_findings: list[ReviewElementFinding]
    total_score: int = Field(default=0, ge=0, le=100)
    score_dimensions: list[ReviewScoreDimension] = Field(default_factory=list)
    recommendations: list[ReviewRecommendation] = Field(default_factory=list)
    turn_diagnostics: list[ReviewTurnDiagnostic] = Field(default_factory=list)
    unresolved_issue_ids: list[str]
    deterministic_conclusion_allowed: bool
    conclusion: str | None
    disclaimer: str
    legal_search_trace_ids: list[str]
    event_sequence_number: int
    created_at: datetime
