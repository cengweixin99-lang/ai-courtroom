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


class SessionEventView(BaseModel):
    sequence_number: int
    phase: CourtPhase
    actor_role: Role
    action: CourtAction | Literal["session_created"]
    payload: SessionEventPayload
    created_at: datetime


class SessionActionResponse(BaseModel):
    session: SessionView
    event: SessionEventView
    agent_invoked: bool
    fixed_response: str


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
