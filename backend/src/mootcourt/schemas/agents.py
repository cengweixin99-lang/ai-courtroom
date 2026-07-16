from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mootcourt.domain.courtroom import CourtAction, CourtPhase, Role
from mootcourt.schemas.case_package import StatementRecord


class StrictAgentModel(BaseModel):
    """Agent 边界统一拒绝未知字段，避免模型输出被静默忽略。"""

    model_config = ConfigDict(extra="forbid")


class AgentRole(StrEnum):
    PROSECUTION = "prosecution"
    DEFENSE = "defense"
    DEFENDANT = "defendant"
    WITNESS = "witness"


class AgentOutputKind(StrEnum):
    ADVOCATE = "advocate"
    WITNESS = "witness"
    DEFENDANT = "defendant"


class Certainty(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClaimType(StrEnum):
    SUPPORTED_FACT = "supported_fact"
    DISPUTED_FACT = "disputed_fact"
    INFERENCE = "inference"
    OPINION = "opinion"


class AgentClaim(StrictAgentModel):
    text: str = Field(min_length=1, max_length=2_000, description="单项事实主张或推论")
    claim_type: ClaimType = Field(description="主张性质")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="支持该主张且当前角色有权访问的证据标识",
    )


class AdvocateOutput(StrictAgentModel):
    kind: Literal[AgentOutputKind.ADVOCATE] = AgentOutputKind.ADVOCATE
    speaker_role: Literal[AgentRole.PROSECUTION, AgentRole.DEFENSE]
    speech: str = Field(min_length=1, max_length=10_000)
    claims: list[AgentClaim] = Field(default_factory=list)
    requested_action: CourtAction | None = None
    target_id: str | None = None


class WitnessOutput(StrictAgentModel):
    kind: Literal[AgentOutputKind.WITNESS] = AgentOutputKind.WITNESS
    answer: str = Field(min_length=1, max_length=10_000)
    supported_by_statement_ids: list[str] = Field(default_factory=list)
    certainty: Certainty
    refused_reason: str | None = Field(default=None, max_length=2_000)


class DefendantOutput(StrictAgentModel):
    kind: Literal[AgentOutputKind.DEFENDANT] = AgentOutputKind.DEFENDANT
    answer: str = Field(min_length=1, max_length=10_000)
    supported_by_statement_ids: list[str] = Field(default_factory=list)
    new_statement: bool = False
    certainty: Certainty
    refused_reason: str | None = Field(default=None, max_length=2_000)


AgentOutput = Annotated[
    AdvocateOutput | WitnessOutput | DefendantOutput,
    Field(discriminator="kind"),
]


class AgentTurnRequest(StrictAgentModel):
    actor_role: AgentRole = Field(description="本回合由系统调用的 AI 角色")
    action: CourtAction = Field(description="由确定性状态机预先批准的庭审动作")
    participant_id: str | None = Field(
        default=None,
        description="证人或被告人的参与人标识；律师角色不得填写",
    )
    target_id: str | None = Field(default=None, description="动作指向的庭审参与人标识")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="本次举证或质证动作引用的证据标识",
    )
    instruction: str | None = Field(
        default=None,
        max_length=4_000,
        description="当前庭审问题或受控任务说明；会作为不可信输入处理",
    )


class AgentCaseContext(StrictAgentModel):
    case_id: str
    package_version: str
    title: str
    summary: str
    jurisdiction: str


class AgentFactContext(StrictAgentModel):
    id: str
    description: str
    status: str


class AgentEvidenceContext(StrictAgentModel):
    id: str
    title: str
    content: str
    reliability_notes: list[str]


class AgentRoleMaterialContext(StrictAgentModel):
    id: str
    title: str
    objectives: list[str]
    priority_evidence_ids: list[str]
    known_weaknesses: list[str]


class AgentParticipantContext(StrictAgentModel):
    id: str
    participant_type: Literal["defendant", "witness"]
    name: str
    public_profile: str
    statements: list[StatementRecord]
    uncertainties: list[str]
    defense_position: str | None = None


class AgentHistoryEvent(StrictAgentModel):
    sequence_number: int
    phase: CourtPhase
    actor_role: Role
    action: str
    content: str | None = None


class AgentContext(StrictAgentModel):
    case: AgentCaseContext
    actor_role: AgentRole
    phase: CourtPhase
    action: CourtAction
    facts: list[AgentFactContext]
    evidence: list[AgentEvidenceContext]
    role_materials: list[AgentRoleMaterialContext]
    participant: AgentParticipantContext | None
    recent_events: list[AgentHistoryEvent]


class AgentTraceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentTraceView(StrictAgentModel):
    trace_id: str
    session_id: str
    actor_role: AgentRole
    participant_id: str | None
    provider: str
    model: str
    status: AgentTraceStatus
    repair_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_cny: float
    error_code: str | None
    error_message: str | None
    created_at: datetime


class AgentTurnError(StrictAgentModel):
    code: str
    message: str
