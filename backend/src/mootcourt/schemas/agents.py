from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mootcourt.domain.courtroom import CourtAction, CourtPhase, Role
from mootcourt.schemas.case_package import StatementRecord


# 所有Agent模型的基类,extra="forbid"拒绝未知字段
class StrictAgentModel(BaseModel):
    """Agent 边界统一拒绝未知字段，避免模型输出被静默忽略。"""

    model_config = ConfigDict(extra="forbid")


# AI角色（公诉、辩护、被告、证人）
class AgentRole(StrEnum):
    PROSECUTION = "prosecution"
    DEFENSE = "defense"
    DEFENDANT = "defendant"
    WITNESS = "witness"


# 输出类型判别器（公诉/辩护、证人、被告）
class AgentOutputKind(StrEnum):
    ADVOCATE = "advocate"
    WITNESS = "witness"
    DEFENDANT = "defendant"


# 确定性等级
class Certainty(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# 主张属性
class ClaimType(StrEnum):
    SUPPORTED_FACT = "supported_fact"
    DISPUTED_FACT = "disputed_fact"
    INFERENCE = "inference"
    OPINION = "opinion"


# 证据引用锚点
class AgentEvidenceCitation(StrictAgentModel):
    evidence_id: str = Field(description="证据标识")
    quote: str = Field(
        min_length=6,
        max_length=500,
        description="可在证据正文或可靠性说明中逐字核验的连续片段",
    )


# 结构化主张
class AgentClaim(StrictAgentModel):
    text: str = Field(min_length=1, max_length=2_000, description="单项事实主张或推论")
    claim_type: ClaimType = Field(description="主张性质")
    fact_ids: list[str] = Field(
        min_length=1,
        description="该主张直接讨论、且能由所引证据关联到的案卷事实标识",
    )
    citations: list[AgentEvidenceCitation] = Field(
        default_factory=list,
        description="支持该主张且属于本轮任务范围的证据原文锚点",
    )


# 律师发言：包含发言正文、结构化主张列表，
# 每项主张必须关联fact_ids（事实依据）和可选的citations（证据原文锚点）
class AdvocateOutput(StrictAgentModel):
    kind: Literal[AgentOutputKind.ADVOCATE] = AgentOutputKind.ADVOCATE
    speaker_role: Literal[AgentRole.PROSECUTION, AgentRole.DEFENSE]
    speech: str = Field(min_length=1, max_length=10_000)
    claims: list[AgentClaim] = Field(
        default_factory=list,
        max_length=6,
        description="本回合最关键的结构化主张，最多六项",
    )
    requested_action: CourtAction | None = None
    target_id: str | None = None


# 陈述引用锚点
class AgentStatementCitation(StrictAgentModel):
    statement_id: str = Field(description="既有陈述标识")
    quote: str = Field(
        min_length=6,
        max_length=500,
        description="可在该既有陈述中逐字核验、并直接出现在回答中的连续片段",
    )


# 证人回答：包含回答、确定性等级、引用既有陈述的锚点、拒绝回答的理由
class WitnessOutput(StrictAgentModel):
    kind: Literal[AgentOutputKind.WITNESS] = AgentOutputKind.WITNESS
    answer: str = Field(min_length=1, max_length=10_000)
    supported_by_statement_ids: list[str] = Field(default_factory=list)
    citations: list[AgentStatementCitation] = Field(default_factory=list)
    certainty: Certainty
    refused_reason: str | None = Field(default=None, max_length=2_000)


# 被告人回答：类似证人、额外有new_statement，标记是否产生新陈述
class DefendantOutput(StrictAgentModel):
    kind: Literal[AgentOutputKind.DEFENDANT] = AgentOutputKind.DEFENDANT
    answer: str = Field(min_length=1, max_length=10_000)
    supported_by_statement_ids: list[str] = Field(default_factory=list)
    citations: list[AgentStatementCitation] = Field(default_factory=list)
    new_statement: bool = False
    certainty: Certainty
    refused_reason: str | None = Field(default=None, max_length=2_000)


AgentOutput = Annotated[
    AdvocateOutput | WitnessOutput | DefendantOutput,
    Field(discriminator="kind"),
]


# 单次调用请求
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
        description="本次举证项目，或质证时允许 Agent 选择引用的证据范围",
    )
    challenge_dimensions: list[str] = Field(
        default_factory=list,
        description="质证维度；Agent 发表证据质证意见时由控制器指定",
    )
    target_event_sequence: int | None = Field(default=None, ge=1)
    instruction: str | None = Field(
        default=None,
        max_length=4_000,
        description="当前庭审问题或受控任务说明；会作为不可信输入处理",
    )


# 案件基本信息（ID、标题、摘要、法域）
class AgentCaseContext(StrictAgentModel):
    case_id: str
    package_version: str
    title: str
    summary: str
    jurisdiction: str


# 事实条目（描述、支持/矛盾证据 ID）
class AgentFactContext(StrictAgentModel):
    id: str
    description: str
    status: str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]


# 证据条目（标题、内容、可靠性说明）
class AgentEvidenceContext(StrictAgentModel):
    id: str
    title: str
    content: str
    reliability_notes: list[str]
    related_fact_ids: list[str]


# 角色材料（目标、优先证据、已知弱点）
class AgentRoleMaterialContext(StrictAgentModel):
    id: str
    title: str
    objectives: list[str]
    priority_evidence_ids: list[str]
    known_weaknesses: list[str]


# 参与人在本次庭审中已经发表的公开陈述
class AgentPublicStatement(StrictAgentModel):
    sequence_number: int
    phase: CourtPhase
    content: str = Field(
        max_length=500,
        description="该参与人在本次庭审中已发表陈述的摘要，超过长度会被截断",
    )


# 参与人信息（公开档案、既有陈述、不确定性）
class AgentParticipantContext(StrictAgentModel):
    id: str
    participant_type: Literal["defendant", "witness"]
    name: str
    public_profile: str
    statements: list[StatementRecord]
    uncertainties: list[str]
    public_statements: list[AgentPublicStatement] = Field(
        default_factory=list,
        description="该参与人在本次庭审中已经发表的公开陈述摘要，用于保证多轮回答一致性",
    )
    defense_position: str | None = None


# 庭审事件重要性分级
class AgentEventImportance(StrEnum):
    CRITICAL = "critical"  # 阶段推进、证据提交、程序请求裁决
    NORMAL = "normal"  # 一般发言、质证
    FILLER = "filler"  # 系统提示、自动推进


# 庭审阶段摘要：用结构化摘要降低 Agent 对原始事件的依赖
class PhaseSummary(StrictAgentModel):
    phase: CourtPhase
    established_facts: list[str] = Field(default_factory=list)
    submitted_evidence_ids: list[str] = Field(default_factory=list)
    challenged_evidence_ids: list[str] = Field(default_factory=list)
    key_statements: list[str] = Field(default_factory=list)
    procedural_rulings: list[str] = Field(default_factory=list)


# 近期庭审事件历史
class AgentHistoryEvent(StrictAgentModel):
    sequence_number: int
    phase: CourtPhase
    actor_role: Role
    action: str
    content: str | None = None
    importance: AgentEventImportance = AgentEventImportance.NORMAL
    summary: str | None = Field(
        default=None,
        description="事件的压缩摘要，用于替代长 content 节省 Token",
    )


# 当前任务参数
class AgentTaskContext(StrictAgentModel):
    target_id: str | None
    evidence_ids: list[str]
    challenge_dimensions: list[str]


# 律师角色在本次庭审中已经提出的公开主张
class AgentPublicClaim(StrictAgentModel):
    sequence_number: int
    phase: CourtPhase
    text: str = Field(max_length=300, description="claim 文本摘要")
    fact_ids: list[str]
    claim_type: ClaimType


# 案件白名单法源（供律师主张法律依据时引用）
class AgentLegalSourceContext(StrictAgentModel):
    source_id: str
    instrument_title: str
    article_number: str
    category: Literal["substantive", "procedure", "evidence_rule"]
    text: str = Field(max_length=1_000, description="条文快照，超长会被截断")


# 顶层聚合
class AgentContext(StrictAgentModel):
    case: AgentCaseContext
    actor_role: AgentRole
    phase: CourtPhase
    action: CourtAction
    task: AgentTaskContext
    facts: list[AgentFactContext]
    evidence: list[AgentEvidenceContext]
    role_materials: list[AgentRoleMaterialContext]
    participant: AgentParticipantContext | None
    phase_summaries: list[PhaseSummary] = Field(
        default_factory=list,
        description="已完成阶段的结构化摘要，帮助 Agent 把握全局进展",
    )
    role_public_claims: list[AgentPublicClaim] = Field(
        default_factory=list,
        description="本角色在本次庭审中已经提出的公开主张，用于防止控辩双方前后矛盾",
    )
    opposing_public_claims: list[AgentPublicClaim] = Field(
        default_factory=list,
        description="对方律师在本次庭审中已经提出的公开主张，用于组织针对性回应",
    )
    legal_sources: list[AgentLegalSourceContext] = Field(
        default_factory=list,
        description="案件 LegalProfile 白名单法源，律师主张法律依据时只能引用其中条款",
    )
    recent_events: list[AgentHistoryEvent]


# 调用状态枚举
class AgentTraceStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# 调用Trace
class AgentTraceView(StrictAgentModel):
    trace_id: str
    session_id: str
    actor_role: AgentRole
    participant_id: str | None
    provider: str
    model: str
    status: AgentTraceStatus
    repair_count: int
    output_normalized: bool
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_cny: float
    error_code: str | None
    error_message: str | None
    created_at: datetime


# 会话累计用量统计
class AgentUsageView(StrictAgentModel):
    """庭审会话累计模型用量；统计失败调用和修复调用消耗，不作为默认阻断条件。"""

    trace_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_cny: float = Field(ge=0)


# 调用错误
class AgentTurnError(StrictAgentModel):
    code: str
    message: str
