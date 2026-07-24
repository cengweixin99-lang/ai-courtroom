import type { AgentRole, CourtAction, CourtPhase, UserRole } from './types'

export const roleLabels: Record<UserRole | AgentRole | 'controller', string> = {
  prosecution: '公诉方',
  defense: '辩护方',
  defendant: '被告人',
  witness: '证人',
  controller: '庭审控制器',
}

export const phaseLabels: Record<CourtPhase, string> = {
  COURT_OPENING: '开庭说明',
  INDICTMENT_AND_DEFENDANT_STATEMENT: '起诉与被告陈述',
  COURT_INVESTIGATION: '法庭调查',
  PROSECUTION_EVIDENCE_AND_EXAMINATION: '公诉方举证质证',
  DEFENSE_EVIDENCE_AND_EXAMINATION: '辩方举证质证',
  WITNESS_QUESTIONING: '证人询问',
  COURT_DEBATE_PROSECUTION: '公诉方辩论',
  COURT_DEBATE_DEFENSE: '辩方辩论',
  DEFENDANT_FINAL_STATEMENT: '被告最后陈述',
  LEGAL_ANALYSIS: '法律分析',
  REVIEW: '教学复盘',
  COMPLETED: '庭审完成',
}

export const actionLabels: Record<CourtAction, string> = {
  advance_phase: '推进阶段',
  make_statement: '发表陈述',
  submit_evidence: '提交证据',
  question_participant: '询问参与人',
  raise_procedural_request: '提出程序请求',
  challenge_evidence: '发表质证意见',
  state_no_objection: '对证据无异议',
  generate_legal_analysis: '生成法律分析',
  view_review: '查看复盘',
  complete_phase: '本阶段发言完毕',
}

export const legalQueries = [
  '什么行为是犯罪，情节显著轻微危害不大是否认为是犯罪',
  '明知行为会发生危害社会结果并希望或者放任结果发生是否属于故意犯罪',
  '已满十六周岁的人犯罪是否应当负刑事责任',
  '盗窃罪第二百六十四条的入罪条件',
  '盗窃公私财物数额较大的金额标准',
  '被盗财物有有效价格证明时怎样认定盗窃数额',
]

export const proceduralRequestTypeLabels: Record<string, string> = {
  IRRELEVANT_QUESTION: '无关问题',
  REPETITIVE_QUESTION: '重复发问',
  IMPROPER_QUESTION: '不当发问',
  EVIDENCE_CHALLENGE: '证据异议',
}

export const proceduralResolutionLabels: Record<string, string> = {
  APPROVED: '准许',
  REJECTED: '驳回',
  RECORDED: '记录在案',
}

export const statementConsistencyLabels: Record<string, string> = {
  SUPPORTED_BY_PRIOR_STATEMENT: '与先前陈述一致',
  EXPLICIT_REFUSAL: '明确拒答',
  UNSUPPORTED: '与先前陈述矛盾',
  NEW_STATEMENT_PENDING_REVIEW: '新增陈述待审核',
}

export const statementReviewStatusLabels: Record<string, string> = {
  INCLUDED_IN_RECORD: '已纳入记录',
  EXCLUDED_FROM_RECORD: '已排除记录',
}

export const evidenceChallengeDimensionLabels: Record<string, string> = {
  AUTHENTICITY: '真实性',
  LEGALITY: '合法性',
  RELEVANCE: '关联性',
  PROBATIVE_VALUE: '证明力',
}

export const businessErrorLabels: Record<string, string> = {
  action_not_allowed: '当前阶段或席位不能执行该操作。',
  content_required: '请填写操作内容。',
  evidence_required: '请至少选择一项证据。',
  evidence_not_submitted: '该证据尚未提交，不能质证。',
  evidence_already_submitted: '该证据已经提交。',
  challenge_dimension_required: '请至少选择一个质证维度。',
  evidence_already_addressed: '该证据已经完成回应，不能重复处理。',
  evidence_agenda_missing: '该证据尚未进入本阶段质证议程。',
  statement_evidence_not_submitted: '陈述只能绑定已经在本庭提交的证据。',
  target_required: '请选择询问对象。',
  target_event_required: '请选择要处理的发问记录。',
  procedural_request_review_pending: '请先处理全部程序请求。',
  new_statement_review_pending: '请先审核全部本庭新增陈述。',
  insufficient_legal_authority: '必要法源检索不足，不能生成复盘。',
  court_review_already_exists: '本庭复盘已经生成。',
  session_time_budget_exceeded: '本庭累计模型调用时间已达到上限，请退出并新建一场庭审。',
  session_token_budget_exceeded: '本庭模型 Token 预算已达到上限，请退出并新建一场庭审。',
  session_cost_budget_exceeded: '本庭模型费用预算已达到上限，请退出并新建一场庭审。',
  turn_limit_reached: '本庭回合数已达到上限，请退出并新建一场庭审。',
  llm_not_configured: '真实 LLM 尚未配置，自动庭审无法继续。',
  agent_provider_timeout: '其他角色生成发言超时，请稍后重试。',
  agent_provider_unavailable: '真实 LLM 服务在自动重试后仍不可用，请稍后重试本阶段。',
  agent_provider_incomplete: '模型输出在自动精简重生成后仍被截断，请重试本次 Agent。',
  agent_invocation_in_progress: '本庭已有 Agent 请求正在生成，请稍后重试以读取同一结果。',
  idempotency_key_reused: '请求标识已用于其他操作，请刷新庭审状态后重试。',
}
