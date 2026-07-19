import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Bot,
  Check,
  ChevronRight,
  FileCheck2,
  FileText,
  Gavel,
  LoaderCircle,
  MessageSquareText,
  Scale,
  ShieldCheck,
  Users,
} from 'lucide-react'

import { api, ApiError } from '../api'
import { actionLabels, businessErrorLabels, legalQueries, phaseLabels, roleLabels } from '../config'
import type {
  AgentRole,
  AgentTurnPayload,
  CaseView,
  CourtAction,
  CourtReview,
  EvidenceStatus,
  ProceduralRequest,
  SessionActionPayload,
  SessionEvent,
  SessionView,
  StatementTrace,
} from '../types'

type CaseTab = 'summary' | 'evidence' | 'participants' | 'strategy'
type StatusTab = 'evidence' | 'requests' | 'statements'

interface Props {
  initialCase: CaseView
  initialSession: SessionView
  onExit: () => void
  onReview: (review: CourtReview) => void
}

interface AgentOption {
  label: string
  actorRole: AgentRole
  action: CourtAction
  participantId?: string
  needsEvidence?: boolean
}

const orchestratedActions: CourtAction[] = ['generate_legal_analysis', 'view_review']

function defaultAction(session: SessionView): CourtAction {
  return session.allowed_actions.find((item) => item !== 'advance_phase' && !orchestratedActions.includes(item))
    ?? session.allowed_actions.find((item) => !orchestratedActions.includes(item))
    ?? session.allowed_actions[0]
    ?? 'advance_phase'
}

async function fetchSessionData(active: SessionView) {
  const [events, evidenceStatuses, requests, statementTraces] = await Promise.all([
    api.getEvents(active.session_id),
    api.getEvidenceStatuses(active.session_id),
    api.getProceduralRequests(active.session_id),
    api.getStatementTraces(active.session_id),
  ])
  return { events, evidenceStatuses, requests, statementTraces }
}

function eventContent(event: SessionEvent): string {
  if (event.payload.agent_output?.speech) return event.payload.agent_output.speech
  if (event.payload.agent_output?.answer) return event.payload.agent_output.answer
  if (event.payload.content) return event.payload.content
  if (event.action === 'session_created') return '庭审会话已创建，案件版本和用户席位已锁定。'
  if (event.action === 'advance_phase') return `庭审已推进至 ${phaseLabels[event.payload.resulting_phase ?? event.phase]}。`
  if (event.action === 'submit_evidence') return `已提交证据 ${event.payload.evidence_ids?.join('、') ?? ''}。`
  if (event.action === 'procedural_request_resolved') return `程序请求处理结果：${event.payload.resolution ?? ''}`
  if (event.action === 'court_review_generated') return '结构化教学复盘已生成。'
  return actionLabels[event.action as CourtAction] ?? event.action
}

function agentOptions(session: SessionView, caseView: CaseView): AgentOption[] {
  const otherRole: AgentRole = session.user_role === 'prosecution' ? 'defense' : 'prosecution'
  const witnesses = caseView.participants.filter((item) => item.participant_type === 'witness')
  switch (session.phase) {
    case 'INDICTMENT_AND_DEFENDANT_STATEMENT':
      return [
        ...(session.user_role === 'defense' ? [{ label: '公诉方宣读意见', actorRole: 'prosecution' as const, action: 'make_statement' as const }] : []),
        { label: '被告人陈述', actorRole: 'defendant', action: 'make_statement', participantId: 'D01' },
      ]
    case 'COURT_INVESTIGATION':
      return [
        { label: `${roleLabels[otherRole]}陈述`, actorRole: otherRole, action: 'make_statement' },
        { label: '被告人陈述', actorRole: 'defendant', action: 'make_statement', participantId: 'D01' },
      ]
    case 'PROSECUTION_EVIDENCE_AND_EXAMINATION':
      return session.user_role === 'defense' ? [{ label: '公诉方提交证据', actorRole: 'prosecution', action: 'submit_evidence', needsEvidence: true }] : []
    case 'DEFENSE_EVIDENCE_AND_EXAMINATION':
      return session.user_role === 'prosecution' ? [{ label: '辩方提交证据', actorRole: 'defense', action: 'submit_evidence', needsEvidence: true }] : []
    case 'WITNESS_QUESTIONING':
      return [
        { label: `${roleLabels[otherRole]}发问`, actorRole: otherRole, action: 'question_participant', participantId: witnesses[0]?.id },
        ...witnesses.map((item) => ({ label: `${item.name}作证`, actorRole: 'witness' as const, action: 'make_statement' as const, participantId: item.id })),
      ]
    case 'COURT_DEBATE_PROSECUTION':
      return session.user_role === 'defense' ? [{ label: '公诉方辩论', actorRole: 'prosecution', action: 'make_statement' }] : []
    case 'COURT_DEBATE_DEFENSE':
      return session.user_role === 'prosecution' ? [{ label: '辩方辩论', actorRole: 'defense', action: 'make_statement' }] : []
    case 'DEFENDANT_FINAL_STATEMENT':
      return [{ label: '被告人最后陈述', actorRole: 'defendant', action: 'make_statement', participantId: 'D01' }]
    default:
      return []
  }
}

export function CourtroomPage({ initialCase, initialSession, onExit, onReview }: Props) {
  const [session, setSession] = useState(initialSession)
  const [events, setEvents] = useState<SessionEvent[]>([])
  const [evidenceStatuses, setEvidenceStatuses] = useState<EvidenceStatus[]>([])
  const [requests, setRequests] = useState<ProceduralRequest[]>([])
  const [statementTraces, setStatementTraces] = useState<StatementTrace[]>([])
  const [caseTab, setCaseTab] = useState<CaseTab>('summary')
  const [statusTab, setStatusTab] = useState<StatusTab>('evidence')
  const [action, setAction] = useState<CourtAction>(defaultAction(initialSession))
  const [content, setContent] = useState('')
  const [targetId, setTargetId] = useState(initialCase.participants.find((item) => item.participant_type === 'witness')?.id ?? '')
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>([])
  const [selectedAgentEvidence, setSelectedAgentEvidence] = useState<string[]>([])
  const [challengeDimensions, setChallengeDimensions] = useState<string[]>(['AUTHENTICITY'])
  const [requestType, setRequestType] = useState('IMPROPER_QUESTION')
  const [targetSequence, setTargetSequence] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const transcriptEnd = useRef<HTMLDivElement>(null)

  const handleError = useCallback((caught: unknown) => {
    if (caught instanceof ApiError) setError(businessErrorLabels[caught.code] ?? `${caught.code}: ${caught.message}`)
    else setError('请求失败，请检查 API 服务。')
  }, [])

  const commitSessionData = useCallback((active: SessionView, data: Awaited<ReturnType<typeof fetchSessionData>>) => {
    setSession(active)
    setEvents(data.events)
    setEvidenceStatuses(data.evidenceStatuses)
    setRequests(data.requests)
    setStatementTraces(data.statementTraces)
    setAction(defaultAction(active))
    setContent('')
    setSelectedEvidence([])
    setSelectedAgentEvidence([])
    setError(null)
  }, [])

  const loadSessionData = useCallback(async (active: SessionView) => {
    const data = await fetchSessionData(active)
    commitSessionData(active, data)
  }, [commitSessionData])

  useEffect(() => {
    // 四组状态使用同一个会话快照刷新，避免庭审记录和审核队列彼此错位。
    let active = true
    void fetchSessionData(initialSession)
      .then((data) => { if (active) commitSessionData(initialSession, data) })
      .catch(handleError)
    return () => { active = false }
  }, [commitSessionData, handleError, initialSession])
  useEffect(() => {
    const marker = transcriptEnd.current
    if (marker && typeof marker.scrollIntoView === 'function') {
      marker.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [events.length])

  // 法律检索和复盘读取由专用流程编排，避免用户误触同名的底层状态机动作。
  const legalActions = useMemo(
    () => session.allowed_actions.filter((item) => !orchestratedActions.includes(item)),
    [session.allowed_actions],
  )
  const aiOptions = useMemo(() => agentOptions(session, initialCase), [session, initialCase])
  const questionEvents = events.filter((item) => item.action === 'question_participant')
  const pendingRequests = requests.filter((item) => !item.resolution)
  const pendingStatements = statementTraces.filter((item) => item.new_statement && !item.review_status)
  const remainingTurns = Math.max(0, 40 - session.turns_used)

  const submitAction = async () => {
    setBusy(true); setError(null)
    try {
      const payload: SessionActionPayload = { action }
      if (['make_statement', 'question_participant', 'challenge_evidence', 'raise_procedural_request'].includes(action)) payload.content = content.trim()
      if (action === 'question_participant') payload.target_id = targetId
      if (action === 'submit_evidence' || action === 'challenge_evidence') payload.evidence_ids = selectedEvidence
      if (action === 'challenge_evidence') payload.challenge_dimensions = challengeDimensions
      if (action === 'raise_procedural_request') {
        payload.procedural_request_type = requestType; if (targetSequence) payload.target_event_sequence = targetSequence
      }
      const result = await api.applyAction(session.session_id, payload)
      await loadSessionData(result.session)
      setContent(''); setSelectedEvidence([])
    } catch (caught) { handleError(caught) } finally { setBusy(false) }
  }

  const runAgent = async (option: AgentOption) => {
    setBusy(true); setError(null)
    try {
      const payload: AgentTurnPayload = { actor_role: option.actorRole, action: option.action }
      if (option.participantId) {
        if (option.actorRole === 'witness' || option.actorRole === 'defendant') payload.participant_id = option.participantId
        if (option.action === 'question_participant') payload.target_id = option.participantId
      }
      if (option.needsEvidence) payload.evidence_ids = selectedAgentEvidence
      if (content.trim()) payload.instruction = content.trim()
      const result = await api.runAgent(session.session_id, payload)
      if (result.status === 'failed') throw new ApiError(result.error?.code ?? 'agent_failed', result.error?.message ?? 'Agent 调用失败', 502)
      await loadSessionData(result.session)
      setContent(''); setSelectedEvidence([]); setSelectedAgentEvidence([])
    } catch (caught) { handleError(caught) } finally { setBusy(false) }
  }

  const resolveProcedure = async (item: ProceduralRequest, resolution: string) => {
    setBusy(true); setError(null)
    try {
      await api.resolveRequest(session.session_id, item.id, resolution, resolution === 'RECORDED' ? '质证意见记入教学评议。' : '由教学控制者根据庭审记录处理。')
      await loadSessionData(session)
    } catch (caught) { handleError(caught) } finally { setBusy(false) }
  }

  const resolveStatement = async (item: StatementTrace, resolution: string) => {
    setBusy(true); setError(null)
    try {
      await api.resolveStatement(session.session_id, item.id, resolution, '作为本庭新增陈述审核，不自动认定相关事实。')
      await loadSessionData(session)
    } catch (caught) { handleError(caught) } finally { setBusy(false) }
  }

  const generateReview = async () => {
    setBusy(true); setError(null)
    try {
      const searchResults = await Promise.all(legalQueries.map((query) => api.searchLegal(session.case_id, query)))
      const insufficient = searchResults.some((item) => item.outcome !== 'SUFFICIENT_LEGAL_AUTHORITY')
      if (insufficient) throw new ApiError('insufficient_legal_authority', '必要法源检索不足', 422)
      const review = await api.createReview(session.session_id, searchResults.map((item) => item.trace_id))
      await loadSessionData(session); onReview(review)
    } catch (caught) {
      // 页面刷新后会话可能仍停留在法律分析，但复盘已经持久化；此时直接恢复既有报告。
      if (caught instanceof ApiError && caught.code === 'court_review_already_exists') {
        try {
          onReview(await api.getReview(session.session_id))
        } catch (reviewError) {
          handleError(reviewError)
        }
      } else {
        handleError(caught)
      }
    } finally { setBusy(false) }
  }

  return (
    <main className="courtroom-shell">
      <header className="courtroom-header">
        <button className="brand brand-button" onClick={onExit}><Scale aria-hidden="true" size={21} /><span>MootCourt Lab</span></button>
        <div className="phase-status">
          <div><span>当前阶段</span><strong>{phaseLabels[session.phase]}</strong></div>
          <div><span>你的席位</span><strong>{roleLabels[session.user_role]}</strong></div>
          <div><span>剩余回合</span><strong>{remainingTurns}</strong></div>
          <div><span>已提交证据</span><strong>{session.submitted_evidence_ids.length} / {initialCase.evidence.length}</strong></div>
        </div>
      </header>

      <section className="workspace">
        <aside className="workspace-panel case-files">
          <div className="panel-title"><BookOpen size={18} /><h2>可阅案卷</h2></div>
          <nav aria-label="案卷目录">
            {([
              ['summary', '案件摘要', FileText], ['evidence', '证据目录', FileCheck2],
              ['participants', '参与人', Users], ['strategy', `${roleLabels[session.user_role]}材料`, ShieldCheck],
            ] as const).map(([id, label, Icon]) => (
              <button className={caseTab === id ? 'file-item active' : 'file-item'} key={id} onClick={() => setCaseTab(id)}><Icon size={17} /><span>{label}</span><ChevronRight size={15} /></button>
            ))}
          </nav>
          <CasePanel active={caseTab} caseView={initialCase} statuses={evidenceStatuses} />
        </aside>

        <section className="transcript" aria-labelledby="transcript-title">
          <div className="panel-title"><Gavel size={18} /><h2 id="transcript-title">公开庭审记录</h2><span className="live-status">记录中</span></div>
          <div className="transcript-body">
            {events.map((event) => (
              <article className={`record-entry role-${event.actor_role}`} key={event.sequence_number}>
                <div className="speaker"><span>{roleLabels[event.actor_role]}</span><small>#{event.sequence_number} · {phaseLabels[event.phase]}</small></div>
                <p>{eventContent(event)}</p>
                {!!event.payload.evidence_ids?.length && <div className="record-tags">{event.payload.evidence_ids.map((id) => <span key={id}>{id}</span>)}</div>}
              </article>
            ))}
            <div ref={transcriptEnd} />
          </div>
        </section>

        <aside className="workspace-panel status-panel">
          <div className="panel-tabs" role="tablist">
            <button className={statusTab === 'evidence' ? 'active' : ''} onClick={() => setStatusTab('evidence')}>证据</button>
            <button className={statusTab === 'requests' ? 'active' : ''} onClick={() => setStatusTab('requests')}>请求 {pendingRequests.length || ''}</button>
            <button className={statusTab === 'statements' ? 'active' : ''} onClick={() => setStatusTab('statements')}>陈述 {pendingStatements.length || ''}</button>
          </div>
          <StatusPanel active={statusTab} statuses={evidenceStatuses} requests={requests} traces={statementTraces} busy={busy} onResolveProcedure={resolveProcedure} onResolveStatement={resolveStatement} />
        </aside>
      </section>

      <footer className="action-dock">
        <div className="action-topline">
          <div className="action-tabs" role="tablist" aria-label="当前合法操作">
            {legalActions.map((item) => <button key={item} className={action === item ? 'active' : ''} onClick={() => setAction(item)}>{actionLabels[item]}</button>)}
          </div>
          <button className="ghost-action" onClick={onExit}><ArrowLeft size={16} />退出会话</button>
        </div>
        <div className="action-form">
          <ActionFields action={action} content={content} setContent={setContent} targetId={targetId} setTargetId={setTargetId} participants={initialCase.participants} statuses={evidenceStatuses} selectedEvidence={selectedEvidence} setSelectedEvidence={setSelectedEvidence} challengeDimensions={challengeDimensions} setChallengeDimensions={setChallengeDimensions} requestType={requestType} setRequestType={setRequestType} targetSequence={targetSequence} setTargetSequence={setTargetSequence} questionEvents={questionEvents} />
          {aiOptions.some((option) => option.needsEvidence) && (
            <label className="agent-evidence-field">
              <span>AI 补位举证</span>
              <select
                multiple
                value={selectedAgentEvidence}
                onChange={(event) => setSelectedAgentEvidence(Array.from(event.target.selectedOptions, (option) => option.value))}
              >
                {evidenceStatuses.filter((item) => item.status !== 'submitted').map((item) => (
                  <option value={item.evidence_id} key={item.evidence_id}>{item.evidence_id} · {item.title}</option>
                ))}
              </select>
            </label>
          )}
          <div className="action-commands">
            {session.phase === 'LEGAL_ANALYSIS' && <button className="primary-action compact" disabled={busy || pendingRequests.length > 0 || pendingStatements.length > 0} onClick={() => void generateReview()}><Scale size={17} />生成复盘</button>}
            {(session.phase === 'REVIEW' || session.phase === 'COMPLETED') && <button className="primary-action compact" disabled={busy} onClick={() => void api.getReview(session.session_id).then(onReview).catch(handleError)}><BookOpen size={17} />查看复盘</button>}
            {aiOptions.map((option) => <button className="secondary-action" key={`${option.label}-${option.participantId ?? ''}`} disabled={busy || (option.needsEvidence && selectedAgentEvidence.length === 0)} onClick={() => void runAgent(option)}><Bot size={17} />{option.label}</button>)}
            {legalActions.length > 0 && <button className="primary-action compact" disabled={busy} onClick={() => void submitAction()}>{busy ? <LoaderCircle className="spin" size={18} /> : action === 'advance_phase' ? <ArrowRight size={18} /> : <Check size={18} />}{actionLabels[action]}</button>}
          </div>
        </div>
        <div className="action-message" aria-live="polite">{error ?? `${phaseLabels[session.phase]} · 第 ${events.length} 条记录`}</div>
      </footer>
    </main>
  )
}

function CasePanel({ active, caseView, statuses }: { active: CaseTab; caseView: CaseView; statuses: EvidenceStatus[] }) {
  if (active === 'summary') return <div className="case-panel-body"><h3>{caseView.case.title}</h3><p>{caseView.case.summary}</p><h4>争议事项</h4>{caseView.case.disputed_issues.map((item) => <div className="brief-item" key={item.id}><strong>{item.title}</strong><p>{item.description}</p></div>)}</div>
  if (active === 'evidence') return <div className="case-panel-body dense-list">{caseView.evidence.map((item) => <div className="brief-item" key={item.id}><strong>{item.id} · {item.title}</strong><small>{statuses.find((status) => status.evidence_id === item.id)?.status === 'submitted' ? '已提交' : '未提交'}</small><p>{item.content}</p></div>)}</div>
  if (active === 'participants') return <div className="case-panel-body dense-list">{caseView.participants.map((item) => <div className="brief-item" key={item.id}><strong>{item.name} · {item.participant_type === 'witness' ? '证人' : '被告人'}</strong><p>{item.public_profile}</p></div>)}</div>
  return <div className="case-panel-body dense-list">{caseView.role_materials.map((item) => <div key={item.id}><h3>{item.title}</h3><h4>本方目标</h4><ul>{item.objectives.map((text) => <li key={text}>{text}</li>)}</ul><h4>已知弱点</h4><ul>{item.known_weaknesses.map((text) => <li key={text}>{text}</li>)}</ul></div>)}</div>
}

interface StatusProps {
  active: StatusTab; statuses: EvidenceStatus[]; requests: ProceduralRequest[]; traces: StatementTrace[]; busy: boolean
  onResolveProcedure: (item: ProceduralRequest, resolution: string) => Promise<void>
  onResolveStatement: (item: StatementTrace, resolution: string) => Promise<void>
}

function StatusPanel({ active, statuses, requests, traces, busy, onResolveProcedure, onResolveStatement }: StatusProps) {
  if (active === 'evidence') return <div className="status-body">{statuses.map((item) => <div className="status-row" key={item.evidence_id}><span>{item.evidence_id}</span><strong>{item.title}</strong><small className={item.status}>{item.status === 'submitted' ? '已提交' : '未提交'}</small></div>)}</div>
  if (active === 'requests') return <div className="status-body">{requests.length === 0 ? <EmptyStatus label="暂无程序请求" /> : requests.map((item) => <div className="review-queue-item" key={item.id}><strong>{item.request_type}</strong><p>{item.content}</p>{item.resolution ? <small>已处理 · {item.resolution}</small> : <div className="inline-actions">{item.request_type === 'EVIDENCE_CHALLENGE' ? <button disabled={busy} onClick={() => void onResolveProcedure(item, 'RECORDED')}>记入评议</button> : <><button disabled={busy} onClick={() => void onResolveProcedure(item, 'APPROVED')}>准许</button><button disabled={busy} onClick={() => void onResolveProcedure(item, 'REJECTED')}>驳回</button></>}</div>}</div>)}</div>
  return <div className="status-body">{traces.length === 0 ? <EmptyStatus label="暂无参与人陈述" /> : traces.map((item) => <div className="review-queue-item" key={item.id}><strong>{item.participant_id} · {item.consistency_status}</strong><p>{item.answer}</p>{item.new_statement && !item.review_status ? <div className="inline-actions"><button disabled={busy} onClick={() => void onResolveStatement(item, 'INCLUDED_IN_RECORD')}>纳入记录</button><button disabled={busy} onClick={() => void onResolveStatement(item, 'EXCLUDED_FROM_RECORD')}>排除</button></div> : <small>{item.review_status ?? (item.supported_statement_ids.join('、') || '明确拒答')}</small>}</div>)}</div>
}

function EmptyStatus({ label }: { label: string }) { return <div className="empty-status"><MessageSquareText size={22} /><span>{label}</span></div> }

interface ActionFieldsProps {
  action: CourtAction; content: string; setContent: (value: string) => void; targetId: string; setTargetId: (value: string) => void
  participants: CaseView['participants']; statuses: EvidenceStatus[]; selectedEvidence: string[]; setSelectedEvidence: (value: string[]) => void
  challengeDimensions: string[]; setChallengeDimensions: (value: string[]) => void; requestType: string; setRequestType: (value: string) => void
  targetSequence: number | null; setTargetSequence: (value: number | null) => void; questionEvents: SessionEvent[]
}

function ActionFields(props: ActionFieldsProps) {
  const needsContent = ['make_statement', 'question_participant', 'challenge_evidence', 'raise_procedural_request'].includes(props.action)
  const needsEvidence = ['submit_evidence', 'challenge_evidence'].includes(props.action)
  if (props.action === 'advance_phase' || props.action === 'generate_legal_analysis' || props.action === 'view_review') return <div className="action-context"><Gavel size={19} /><span>{actionLabels[props.action]}</span></div>
  return <div className="field-row">
    {(props.action === 'question_participant') && <label><span>询问对象</span><select value={props.targetId} onChange={(event) => props.setTargetId(event.target.value)}>{props.participants.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.id}</option>)}</select></label>}
    {needsEvidence && <label><span>证据</span><select multiple value={props.selectedEvidence} onChange={(event) => props.setSelectedEvidence(Array.from(event.target.selectedOptions, (option) => option.value))}>{props.statuses.filter((item) => props.action === 'submit_evidence' ? item.status !== 'submitted' : item.status === 'submitted').map((item) => <option value={item.evidence_id} key={item.evidence_id}>{item.evidence_id} · {item.title}</option>)}</select></label>}
    {props.action === 'challenge_evidence' && <label><span>质证维度</span><select multiple value={props.challengeDimensions} onChange={(event) => props.setChallengeDimensions(Array.from(event.target.selectedOptions, (option) => option.value))}>{['AUTHENTICITY', 'LEGALITY', 'RELEVANCE', 'PROBATIVE_VALUE'].map((item) => <option key={item}>{item}</option>)}</select></label>}
    {props.action === 'raise_procedural_request' && <><label><span>请求类型</span><select value={props.requestType} onChange={(event) => props.setRequestType(event.target.value)}><option>IRRELEVANT_QUESTION</option><option>REPETITIVE_QUESTION</option><option>IMPROPER_QUESTION</option></select></label><label><span>目标发问</span><select value={props.targetSequence ?? ''} onChange={(event) => props.setTargetSequence(Number(event.target.value) || null)}><option value="">请选择</option>{props.questionEvents.map((item) => <option key={item.sequence_number} value={item.sequence_number}>#{item.sequence_number} {item.payload.content}</option>)}</select></label></>}
    {needsContent && <label className="grow"><span>内容</span><textarea value={props.content} onChange={(event) => props.setContent(event.target.value)} rows={2} /></label>}
  </div>
}
