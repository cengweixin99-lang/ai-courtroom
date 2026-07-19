from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mootcourt.domain.courtroom import CourtAction, CourtPhase, Role
from mootcourt.schemas.agents import (
    AgentOutput,
    AgentRole,
    AgentTraceStatus,
    AgentTraceView,
    AgentTurnError,
)
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


class UserRole(StrEnum):
    """User-selectable courtroom roles."""

    PROSECUTION = "prosecution"
    DEFENSE = "defense"


class ParticipantType(StrEnum):
    DEFENDANT = "defendant"
    WITNESS = "witness"


class ProceduralRequestType(StrEnum):
    IRRELEVANT_QUESTION = "IRRELEVANT_QUESTION"
    REPETITIVE_QUESTION = "REPETITIVE_QUESTION"
    IMPROPER_QUESTION = "IMPROPER_QUESTION"
    EVIDENCE_CHALLENGE = "EVIDENCE_CHALLENGE"


class EvidenceChallengeDimension(StrEnum):
    AUTHENTICITY = "AUTHENTICITY"
    LEGALITY = "LEGALITY"
    RELEVANCE = "RELEVANCE"
    PROBATIVE_VALUE = "PROBATIVE_VALUE"


class ProceduralRequestStatus(StrEnum):
    PENDING_CONTROLLER_REVIEW = "pending_controller_review"
    RECORDED_FOR_EVALUATION = "recorded_for_evaluation"
    RESOLVED = "resolved"


class ProceduralResolution(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RECORDED = "RECORDED"


class ParticipantConsistencyStatus(StrEnum):
    SUPPORTED_BY_PRIOR_STATEMENT = "SUPPORTED_BY_PRIOR_STATEMENT"
    EXPLICIT_REFUSAL = "EXPLICIT_REFUSAL"
    UNSUPPORTED = "UNSUPPORTED"
    NEW_STATEMENT_PENDING_REVIEW = "NEW_STATEMENT_PENDING_REVIEW"


class EvidenceFactSupportStatus(StrEnum):
    NO_SUBMITTED_SUPPORT = "NO_SUBMITTED_SUPPORT"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    SUPPORTED_BY_SUBMITTED_EVIDENCE = "SUPPORTED_BY_SUBMITTED_EVIDENCE"


class EvidenceSubmissionStatus(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    PENDING = "pending"
    SUBMITTED = "submitted"


class CaseSummary(BaseModel):
    case_id: str
    package_version: str
    title: str
    status: str
    jurisdiction: str
    law_as_of_date: date


class ParticipantView(BaseModel):
    id: str
    participant_type: ParticipantType
    name: str
    public_profile: str
    statements: list[StatementRecord]


class CaseView(BaseModel):
    case_id: str
    package_version: str
    role: UserRole
    case: CaseRecord
    facts: list[FactRecord]
    evidence: list[EvidenceRecord]
    participants: list[ParticipantView]
    role_materials: list[RoleMaterial]
    legal_profile: LegalProfile
    legal_sources: list[LegalSourceRecord]
    procedure_profile: ProcedureProfile


class SessionCreate(BaseModel):
    case_id: str = Field(description="用于创建会话的案件包业务标识")
    user_role: UserRole = Field(description="用户在本次庭审中固定扮演的角色")
    package_version: str | None = Field(
        default=None,
        description="锁定的案件包版本；不传时使用最新导入版本",
    )


class SessionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: CourtAction = Field(description="依据当前庭审阶段执行的动作")
    target_id: str | None = Field(
        default=None,
        description="动作指向的参与人标识；询问参与人时必填",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="动作引用的证据标识；举证或质证时至少提供一个",
    )
    content: str | None = Field(
        default=None,
        max_length=10_000,
        description="陈述、询问或异议的正文；需要表达内容的动作必填",
    )
    procedural_request_type: ProceduralRequestType | None = Field(
        default=None,
        description="问题制止请求类型；raise_procedural_request 时必填",
    )
    target_event_sequence: int | None = Field(
        default=None,
        ge=1,
        description="被请求制止的既有发问事件序号",
    )
    challenge_dimensions: list[EvidenceChallengeDimension] = Field(
        default_factory=list,
        description="质证维度；challenge_evidence 时至少选择一项",
    )


class SessionView(BaseModel):
    session_id: str
    case_id: str
    package_version: str
    user_role: UserRole
    phase: CourtPhase
    status: str
    turns_used: int
    allowed_actions: list[CourtAction]
    submitted_evidence_ids: list[str]
    created_at: datetime
    updated_at: datetime


class SessionEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str | None = None
    package_version: str | None = None
    target_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    content: str | None = None
    resulting_phase: CourtPhase | None = None
    agent_role: AgentRole | None = None
    participant_id: str | None = None
    trace_id: str | None = None
    agent_output: AgentOutput | None = None
    procedural_request_id: str | None = None
    procedural_request_type: ProceduralRequestType | None = None
    procedural_request_status: ProceduralRequestStatus | None = None
    target_event_sequence: int | None = None
    challenge_dimensions: list[EvidenceChallengeDimension] = Field(default_factory=list)
    resolution: ProceduralResolution | None = None
    resolution_reason: str | None = None
    resolution_event_sequence: int | None = None
    statement_trace_id: str | None = None
    statement_review_status: str | None = None
    court_review_id: str | None = None


class EvidenceStatusView(BaseModel):
    evidence_id: str
    title: str
    available_to_current_role: bool
    status: EvidenceSubmissionStatus
    submitted_by: UserRole | None = None
    submitted_at: datetime | None = None


class ProceduralRequestView(BaseModel):
    id: str
    session_id: str
    request_type: ProceduralRequestType
    raised_by: UserRole
    event_sequence_number: int
    target_event_sequence: int | None
    evidence_ids: list[str]
    challenge_dimensions: list[EvidenceChallengeDimension]
    content: str
    status: ProceduralRequestStatus
    resolution: ProceduralResolution | None
    resolution_reason: str | None
    resolved_at: datetime | None
    resolution_event_sequence: int | None
    created_at: datetime


class ProceduralRequestResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: ProceduralResolution = Field(description="教学控制者对程序请求的处理结果")
    reason: str = Field(min_length=1, max_length=4_000, description="处理理由")


class ParticipantStatementTraceView(BaseModel):
    id: str
    session_id: str
    participant_id: str
    actor_role: AgentRole
    event_sequence_number: int
    answer: str
    supported_statement_ids: list[str]
    related_fact_ids: list[str]
    consistency_status: ParticipantConsistencyStatus
    new_statement: bool
    refused_reason: str | None
    review_status: str | None
    review_reason: str | None
    reviewed_at: datetime | None
    review_event_sequence: int | None
    created_at: datetime


class EvidenceFactSummaryView(BaseModel):
    fact_id: str
    description: str
    fact_record_status: str
    related_evidence_ids: list[str]
    submitted_evidence_ids: list[str]
    unsubmitted_evidence_ids: list[str]
    appeared_statement_ids: list[str]
    support_status: EvidenceFactSupportStatus


class SessionEventView(BaseModel):
    sequence_number: int
    phase: CourtPhase
    actor_role: Role
    action: (
        CourtAction
        | Literal[
            "session_created",
            "procedural_request_resolved",
            "new_statement_reviewed",
            "court_review_generated",
        ]
    )
    payload: SessionEventPayload
    created_at: datetime


class SessionActionResponse(BaseModel):
    session: SessionView
    event: SessionEventView
    agent_invoked: bool
    fixed_response: str


class ProceduralRequestResolutionResponse(BaseModel):
    request: ProceduralRequestView
    event: SessionEventView


class AgentTurnResponse(BaseModel):
    status: AgentTraceStatus
    session: SessionView
    event: SessionEventView | None
    output: AgentOutput | None
    trace: AgentTraceView
    error: AgentTurnError | None = None


class ImportResult(BaseModel):
    case_id: str
    package_version: str
    database_id: int
    created: bool
