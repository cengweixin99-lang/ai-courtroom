export type UserRole = 'prosecution' | 'defense'
export type AgentRole = UserRole | 'defendant' | 'witness'

export type CourtPhase =
  | 'COURT_OPENING'
  | 'INDICTMENT_AND_DEFENDANT_STATEMENT'
  | 'COURT_INVESTIGATION'
  | 'PROSECUTION_EVIDENCE_AND_EXAMINATION'
  | 'DEFENSE_EVIDENCE_AND_EXAMINATION'
  | 'WITNESS_QUESTIONING'
  | 'COURT_DEBATE_PROSECUTION'
  | 'COURT_DEBATE_DEFENSE'
  | 'DEFENDANT_FINAL_STATEMENT'
  | 'LEGAL_ANALYSIS'
  | 'REVIEW'
  | 'COMPLETED'

export type CourtAction =
  | 'advance_phase'
  | 'make_statement'
  | 'submit_evidence'
  | 'question_participant'
  | 'raise_procedural_request'
  | 'challenge_evidence'
  | 'state_no_objection'
  | 'generate_legal_analysis'
  | 'view_review'
  | 'complete_phase'

export interface CaseSummary {
  case_id: string
  package_version: string
  title: string
  status: string
  jurisdiction: string
  law_as_of_date: string
}

export interface DisputedIssue {
  id: string
  title: string
  description: string
}

export interface FactRecord {
  id: string
  description: string
  status: string
  supporting_evidence_ids: string[]
  contradicting_evidence_ids: string[]
  materiality: string
}

export interface EvidenceRecord {
  id: string
  type: string
  title: string
  content: string
  source: string
  reliability_notes: string[]
  related_fact_ids: string[]
  status: string
}

export interface ParticipantView {
  id: string
  participant_type: 'defendant' | 'witness'
  name: string
  public_profile: string
  statements: Array<{ id: string; text: string; related_fact_ids: string[]; certainty: string }>
}

export interface RoleMaterial {
  id: string
  role: UserRole
  title: string
  objectives: string[]
  priority_evidence_ids: string[]
  known_weaknesses: string[]
}

export interface CaseView {
  case_id: string
  package_version: string
  role: UserRole
  case: {
    title: string
    summary: string
    charge_draft: string
    law_as_of_date: string
    estimated_duration_minutes: number
    disputed_issues: DisputedIssue[]
    disclaimer: string
  }
  facts: FactRecord[]
  evidence: EvidenceRecord[]
  participants: ParticipantView[]
  role_materials: RoleMaterial[]
  legal_profile: { jurisdiction: string; law_as_of_date: string }
}

export interface SessionView {
  session_id: string
  case_id: string
  package_version: string
  user_role: UserRole
  phase: CourtPhase
  status: string
  turns_used: number
  allowed_actions: CourtAction[]
  submitted_evidence_ids: string[]
  created_at: string
  updated_at: string
}

export interface SessionEvent {
  sequence_number: number
  phase: CourtPhase
  actor_role: 'controller' | AgentRole
  action: CourtAction | 'session_created' | 'procedural_request_resolved' | 'new_statement_reviewed' | 'court_review_generated'
  payload: {
    content?: string | null
    evidence_ids?: string[]
    target_id?: string | null
    resulting_phase?: CourtPhase | null
    procedural_request_id?: string | null
    procedural_request_type?: string | null
    resolution?: string | null
    agent_output?: {
      speech?: string
      answer?: string
    } | null
  }
  created_at: string
}

export interface EvidenceStatus {
  evidence_id: string
  title: string
  available_to_current_role: boolean
  status: 'not_submitted' | 'pending' | 'submitted'
  submitted_by: UserRole | null
  submitted_at: string | null
}

export interface EvidenceAgendaItem {
  id: number
  session_id: string
  phase: CourtPhase
  evidence_id: string
  submitted_by: UserRole
  responding_role: UserRole
  status: 'pending' | 'challenged' | 'no_objection' | 'deferred'
  submission_event_sequence: number | null
  response_event_sequence: number | null
  response_action: CourtAction | null
  challenge_dimensions: string[]
  created_at: string
  updated_at: string
}

export interface ProceduralRequest {
  id: string
  request_type: string
  raised_by: UserRole
  event_sequence_number: number
  target_event_sequence: number | null
  evidence_ids: string[]
  challenge_dimensions: string[]
  content: string
  status: string
  resolution: 'APPROVED' | 'REJECTED' | 'RECORDED' | null
  resolution_reason: string | null
}

export interface StatementTrace {
  id: string
  participant_id: string
  actor_role: AgentRole
  event_sequence_number: number
  answer: string
  supported_statement_ids: string[]
  related_fact_ids: string[]
  consistency_status: string
  new_statement: boolean
  review_status: string | null
  review_reason: string | null
}

export interface SessionActionPayload {
  action: CourtAction
  target_id?: string
  evidence_ids?: string[]
  content?: string
  procedural_request_type?: string
  target_event_sequence?: number
  challenge_dimensions?: string[]
}

export interface ReviewCitation {
  source_id: string
  instrument_title: string
  article_number: string
  text: string
  official_source_url: string | null
  trace_id: string
}

export interface CourtReview {
  id: string
  session_id: string
  jurisdiction: string
  law_as_of_date: string
  burden_of_proof: string
  standard_of_proof: string
  user_role: UserRole | ''
  fact_findings: Array<{
    fact_id: string
    description: string
    status: 'SUPPORTED' | 'DISPUTED' | 'INSUFFICIENT'
    submitted_supporting_evidence_ids: string[]
    submitted_contradicting_evidence_ids: string[]
    appeared_statement_ids: string[]
    challenged_evidence_ids: string[]
  }>
  element_findings: Array<{
    element_id: string
    description: string
    status: 'SATISFIED' | 'NOT_SATISFIED' | 'DISPUTED' | 'INSUFFICIENT' | 'NOT_APPLICABLE'
    supporting_fact_ids: string[]
    contradicting_fact_ids: string[]
    citations: ReviewCitation[]
  }>
  total_score: number
  score_dimensions: Array<{
    key: string
    label: string
    score: number
    numerator: number
    denominator: number
    summary: string
  }>
  recommendations: Array<{
    id: string
    priority: 'high' | 'medium' | 'low'
    title: string
    detail: string
    related_evidence_ids: string[]
    related_fact_ids: string[]
    related_element_ids: string[]
  }>
  turn_diagnostics: Array<{
    event_sequence_number: number
    actor_role: string
    phase: CourtPhase
    action: string
    score: number
    evidence_ids: string[]
    fact_ids: string[]
    checks: Array<{ key: string; label: string; passed: boolean; detail: string }>
    recommendation: string | null
  }>
  unresolved_issue_ids: string[]
  deterministic_conclusion_allowed: boolean
  conclusion: string | null
  disclaimer: string
}

export interface TurnQualityEvaluationReport {
  id: string
  review_id: string
  session_id: string
  provider: string
  model: string
  evaluations: Array<{
    event_sequence_number: number
    organization_score: number
    responsiveness_score: number
    advocacy_score: number
    strengths: string[]
    improvements: string[]
    rewritten_example: string | null
    evidence_ids: string[]
    fact_ids: string[]
  }>
  input_tokens: number
  output_tokens: number
  estimated_cost_cny: number
  repair_count: number
  created_at: string
}

export interface AgentTurnPayload {
  actor_role: AgentRole
  participant_id?: string
  action: CourtAction
  target_id?: string
  evidence_ids?: string[]
  instruction?: string
}

export interface AgentTurnResponse {
  status: 'succeeded' | 'failed'
  session: SessionView
  event: SessionEvent | null
  error: { code: string; message: string } | null
}

export interface AutoStepResponse {
  status: 'progressed' | 'waiting_for_user' | 'waiting_for_review' | 'completed' | 'failed'
  session: SessionView
  event: SessionEvent | null
  message: string
  error: { code: string; message: string } | null
}
