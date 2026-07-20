from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CourtPhase(StrEnum):
    """庭审阶段状态机
    开庭说明 ──→ 公诉与被告陈述 ──→ 法庭调查 ──→ 控方举证质证 ──→ 辩方举证质证
    证人询问 ──→ 法庭辩论(控) ──→ 法庭辩论(辩) ──→ 被告最后陈述 ──→ 法律分析 ──→ 评审 ──→ 结案
    """

    COURT_OPENING = "COURT_OPENING"
    INDICTMENT_AND_DEFENDANT_STATEMENT = "INDICTMENT_AND_DEFENDANT_STATEMENT"
    COURT_INVESTIGATION = "COURT_INVESTIGATION"
    PROSECUTION_EVIDENCE_AND_EXAMINATION = "PROSECUTION_EVIDENCE_AND_EXAMINATION"
    DEFENSE_EVIDENCE_AND_EXAMINATION = "DEFENSE_EVIDENCE_AND_EXAMINATION"
    WITNESS_QUESTIONING = "WITNESS_QUESTIONING"
    COURT_DEBATE_PROSECUTION = "COURT_DEBATE_PROSECUTION"
    COURT_DEBATE_DEFENSE = "COURT_DEBATE_DEFENSE"
    DEFENDANT_FINAL_STATEMENT = "DEFENDANT_FINAL_STATEMENT"
    LEGAL_ANALYSIS = "LEGAL_ANALYSIS"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"


PHASE_SEQUENCE = tuple(CourtPhase)


class Role(StrEnum):
    """角色定义
    庭审流程控制者
    公诉方
    辩护方
    被告人
    证人
    """

    CONTROLLER = "controller"
    PROSECUTION = "prosecution"
    DEFENSE = "defense"
    DEFENDANT = "defendant"
    WITNESS = "witness"


class CourtAction(StrEnum):
    """操作分类
    推进到下一阶段
    发表陈述（公诉/辩护/被告/证人）
    提交证据、质证（质疑证据）
    询问参与方（证人询问阶段）
    提出程序性请求（如回避、延期）
    生成法律分析、查看评审结果
    """

    ADVANCE_PHASE = "advance_phase"
    MAKE_STATEMENT = "make_statement"
    SUBMIT_EVIDENCE = "submit_evidence"
    QUESTION_PARTICIPANT = "question_participant"
    RAISE_PROCEDURAL_REQUEST = "raise_procedural_request"
    CHALLENGE_EVIDENCE = "challenge_evidence"
    GENERATE_LEGAL_ANALYSIS = "generate_legal_analysis"
    VIEW_REVIEW = "view_review"
    COMPLETE_PHASE = "complete_phase"


class ActionRequest(BaseModel):
    """操作请求模型"""

    role: Role  # 发起操作的角色
    action: CourtAction  # 请求执行的操作
    target_id: str | None = None  # 目标对象 id（如询问某个证人）
    evidence_ids: list[str] = Field(default_factory=list)  #   关联的证据id列表（提交/质证时使用）


class ActionDecision(BaseModel):
    """操作决策模型"""

    allowed: bool  # 是否允许操作
    reason: str | None = None  # 拒绝理由（仅allowed = False 时填充）
    invoke_agent: bool = False  # 是否需要调用 AI Agent


# 权限矩阵
_LEGAL_ACTIONS: dict[CourtPhase, dict[Role, frozenset[CourtAction]]] = {
    CourtPhase.COURT_OPENING: {
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.INDICTMENT_AND_DEFENDANT_STATEMENT: {
        Role.PROSECUTION: frozenset({CourtAction.MAKE_STATEMENT, CourtAction.COMPLETE_PHASE}),
        Role.DEFENDANT: frozenset({CourtAction.MAKE_STATEMENT}),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.COURT_INVESTIGATION: {
        Role.PROSECUTION: frozenset({CourtAction.MAKE_STATEMENT, CourtAction.COMPLETE_PHASE}),
        Role.DEFENSE: frozenset({CourtAction.MAKE_STATEMENT, CourtAction.COMPLETE_PHASE}),
        Role.DEFENDANT: frozenset({CourtAction.MAKE_STATEMENT}),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.PROSECUTION_EVIDENCE_AND_EXAMINATION: {
        Role.PROSECUTION: frozenset({CourtAction.SUBMIT_EVIDENCE, CourtAction.COMPLETE_PHASE}),
        Role.DEFENSE: frozenset(
            {
                CourtAction.CHALLENGE_EVIDENCE,
                CourtAction.RAISE_PROCEDURAL_REQUEST,
                CourtAction.COMPLETE_PHASE,
            }
        ),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.DEFENSE_EVIDENCE_AND_EXAMINATION: {
        Role.DEFENSE: frozenset({CourtAction.SUBMIT_EVIDENCE, CourtAction.COMPLETE_PHASE}),
        Role.PROSECUTION: frozenset(
            {
                CourtAction.CHALLENGE_EVIDENCE,
                CourtAction.RAISE_PROCEDURAL_REQUEST,
                CourtAction.COMPLETE_PHASE,
            }
        ),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.WITNESS_QUESTIONING: {
        Role.PROSECUTION: frozenset(
            {
                CourtAction.QUESTION_PARTICIPANT,
                CourtAction.RAISE_PROCEDURAL_REQUEST,
                CourtAction.COMPLETE_PHASE,
            }
        ),
        Role.DEFENSE: frozenset(
            {
                CourtAction.QUESTION_PARTICIPANT,
                CourtAction.RAISE_PROCEDURAL_REQUEST,
                CourtAction.COMPLETE_PHASE,
            }
        ),
        Role.WITNESS: frozenset({CourtAction.MAKE_STATEMENT}),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.COURT_DEBATE_PROSECUTION: {
        Role.PROSECUTION: frozenset({CourtAction.MAKE_STATEMENT, CourtAction.COMPLETE_PHASE}),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.COURT_DEBATE_DEFENSE: {
        Role.DEFENSE: frozenset({CourtAction.MAKE_STATEMENT, CourtAction.COMPLETE_PHASE}),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.DEFENDANT_FINAL_STATEMENT: {
        Role.DEFENDANT: frozenset({CourtAction.MAKE_STATEMENT}),
        Role.CONTROLLER: frozenset({CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.LEGAL_ANALYSIS: {
        Role.CONTROLLER: frozenset(
            {CourtAction.GENERATE_LEGAL_ANALYSIS, CourtAction.ADVANCE_PHASE}
        ),
    },
    CourtPhase.REVIEW: {
        Role.PROSECUTION: frozenset({CourtAction.COMPLETE_PHASE}),
        Role.DEFENSE: frozenset({CourtAction.COMPLETE_PHASE}),
        Role.CONTROLLER: frozenset({CourtAction.VIEW_REVIEW, CourtAction.ADVANCE_PHASE}),
    },
    CourtPhase.COMPLETED: {
        Role.CONTROLLER: frozenset({CourtAction.VIEW_REVIEW}),
    },
}


def validate_action(phase: CourtPhase, request: ActionRequest) -> ActionDecision:
    allowed = request.action in _LEGAL_ACTIONS.get(phase, {}).get(request.role, frozenset())
    if not allowed:
        return ActionDecision(
            allowed=False,
            reason=f"{request.role.value} cannot {request.action.value} during {phase.value}",
            invoke_agent=False,
        )

    invokes_agent = request.role not in {Role.CONTROLLER}
    return ActionDecision(allowed=True, invoke_agent=invokes_agent)


def allowed_actions(phase: CourtPhase, role: Role) -> tuple[CourtAction, ...]:
    return tuple(sorted(_LEGAL_ACTIONS.get(phase, {}).get(role, frozenset()), key=str))


def next_phase(current: CourtPhase) -> CourtPhase:
    index = PHASE_SEQUENCE.index(current)
    if index == len(PHASE_SEQUENCE) - 1:
        return current
    return PHASE_SEQUENCE[index + 1]
