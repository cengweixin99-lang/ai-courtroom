import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  BookOpen,
  BookOpenCheck,
  Check,
  ChevronRight,
  FileCheck2,
  FileText,
  Gavel,
  LoaderCircle,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Scale,
  ShieldCheck,
  Users,
} from 'lucide-react'

import { api, ApiError } from '../api'
import { actionLabels, businessErrorLabels, legalQueries, phaseLabels, roleLabels } from '../config'
import { AccountControls } from './AccountControls'
import { useAccount } from '../auth-context'
import type {
  CaseView,
  CourtAction,
  CourtReview,
  AgentUsage,
  EvidenceStatus,
  EvidenceAgendaItem,
  ProceduralRequest,
  SessionActionPayload,
  SessionEvent,
  SessionView,
  StatementTrace,
} from '../types'

type CaseTab = 'summary' | 'evidence' | 'participants' | 'strategy'
type StatusTab = 'evidence' | 'requests' | 'statements'
type StreamingTurn = {
  actorRole: string
  participantId: string | null
  text: string
  status: 'receiving' | 'validating'
}

interface Props {
  initialCase: CaseView
  initialSession: SessionView
  autoStart?: boolean
  focusedEventSequence?: number | null
  onExit: () => void
  onReview: (review: CourtReview) => void
  reviewAvailable?: boolean
  onOpenReview?: () => void
}

const orchestratedActions: CourtAction[] = ['generate_legal_analysis', 'view_review', 'complete_phase']

function defaultAction(session: SessionView): CourtAction {
  return session.allowed_actions.find((item) => item !== 'advance_phase' && !orchestratedActions.includes(item))
    ?? session.allowed_actions.find((item) => !orchestratedActions.includes(item))
    ?? session.allowed_actions[0]
    ?? 'complete_phase'
}

async function fetchSessionData(active: SessionView) {
  const [events, evidenceStatuses, evidenceAgenda, requests, statementTraces, agentUsage] = await Promise.all([
    api.getEvents(active.session_id),
    api.getEvidenceStatuses(active.session_id),
    api.getEvidenceAgenda(active.session_id),
    api.getProceduralRequests(active.session_id),
    api.getStatementTraces(active.session_id),
    api.getAgentUsage(active.session_id),
  ])
  return { events, evidenceStatuses, evidenceAgenda, requests, statementTraces, agentUsage }
}

const emptyAgentUsage: AgentUsage = {
  trace_count: 0,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  latency_ms: 0,
  estimated_cost_cny: 0,
}

function formatTokens(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)}k` : value.toLocaleString('zh-CN')
}

function eventContent(event: SessionEvent): string {
  if (event.payload.agent_output?.speech) return event.payload.agent_output.speech
  if (event.payload.agent_output?.answer) return event.payload.agent_output.answer
  if (event.payload.content) return event.payload.content
  if (event.action === 'session_created') return '庭审会话已创建，案件版本和用户席位已锁定。'
  if (event.action === 'advance_phase') return `庭审已推进至 ${phaseLabels[event.payload.resulting_phase ?? event.phase]}。`
  if (event.action === 'submit_evidence') return `已提交证据 ${event.payload.evidence_ids?.join('、') ?? ''}。`
  if (event.action === 'state_no_objection') return `对证据 ${event.payload.evidence_ids?.join('、') ?? ''} 无异议。`
  if (event.action === 'procedural_request_resolved') return `程序请求处理结果：${event.payload.resolution ?? ''}`
  if (event.action === 'court_review_generated') return '结构化教学复盘已生成。'
  return actionLabels[event.action as CourtAction] ?? event.action
}

function autoStepStorageKey(sessionId: string): string {
  return `mootcourt:auto-step:${sessionId}`
}

function streamingTurnStorageKey(sessionId: string): string {
  return `mootcourt:streaming-turn:${sessionId}`
}

function restoreStreamingTurn(sessionId: string): StreamingTurn | null {
  try {
    // 没有未完成的幂等请求时不恢复草稿，避免展示已经落库的旧发言。
    if (!sessionStorage.getItem(autoStepStorageKey(sessionId))) return null
    const raw = sessionStorage.getItem(streamingTurnStorageKey(sessionId))
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<StreamingTurn>
    if (typeof value.actorRole !== 'string' || typeof value.text !== 'string') return null
    if (value.status !== 'receiving' && value.status !== 'validating') return null
    return {
      actorRole: value.actorRole,
      participantId: typeof value.participantId === 'string' ? value.participantId : null,
      text: value.text,
      status: value.status,
    }
  } catch {
    return null
  }
}

function waitForReconnect(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, delayMs)
    const abort = () => {
      window.clearTimeout(timeout)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `auto-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

interface TranscriptEntryProps {
  actorRole: string
  userRole: SessionView['user_role']
  content: string
  meta: string
  evidenceIds?: string[]
  eventSequence?: number
  highlighted?: boolean
  streaming?: boolean
}

function transcriptLane(actorRole: string, userRole: SessionView['user_role']): 'left' | 'right' {
  return actorRole === userRole ? 'right' : 'left'
}

// 流式发言和已落库事件共用同一结构，避免完成瞬间因节点样式切换产生跳变。
function TranscriptEntry({ actorRole, userRole, content, meta, evidenceIds = [], eventSequence, highlighted = false, streaming = false }: TranscriptEntryProps) {
  const lane = transcriptLane(actorRole, userRole)
  return (
    <article
      id={eventSequence ? `court-event-${eventSequence}` : undefined}
      data-event-sequence={eventSequence}
      tabIndex={eventSequence ? -1 : undefined}
      className={`record-entry lane-${lane} role-${actorRole}${highlighted ? ' review-highlight' : ''}`}
      aria-live={streaming ? 'polite' : undefined}
      aria-busy={streaming || undefined}
    >
      <div className="speaker">
        <span>{actorRole === 'controller' && <Gavel size={14} aria-hidden="true" />}{roleLabels[actorRole as keyof typeof roleLabels] ?? '庭审角色'}</span>
        <small>{meta}</small>
      </div>
      <p>{content}{streaming && <span className="stream-caret" aria-hidden="true" />}</p>
      {!!evidenceIds.length && (
        <div className="record-tags">{evidenceIds.map((id) => <span key={id}>{id}</span>)}</div>
      )}
    </article>
  )
}

export function CourtroomPage({ initialCase, initialSession, autoStart = true, focusedEventSequence = null, onExit, onReview, reviewAvailable = false, onOpenReview }: Props) {
  const account = useAccount()
  const [session, setSession] = useState(initialSession)
  const [events, setEvents] = useState<SessionEvent[]>([])
  const [evidenceStatuses, setEvidenceStatuses] = useState<EvidenceStatus[]>([])
  const [evidenceAgenda, setEvidenceAgenda] = useState<EvidenceAgendaItem[]>([])
  const [requests, setRequests] = useState<ProceduralRequest[]>([])
  const [statementTraces, setStatementTraces] = useState<StatementTrace[]>([])
  const [agentUsage, setAgentUsage] = useState<AgentUsage>(emptyAgentUsage)
  const [caseTab, setCaseTab] = useState<CaseTab>('summary')
  const [statusTab, setStatusTab] = useState<StatusTab>('evidence')
  const [casePanelCollapsed, setCasePanelCollapsed] = useState(false)
  const [statusPanelCollapsed, setStatusPanelCollapsed] = useState(false)
  const [action, setAction] = useState<CourtAction>(defaultAction(initialSession))
  const [content, setContent] = useState('')
  const [targetId, setTargetId] = useState(initialCase.participants.find((item) => item.participant_type === 'witness')?.id ?? '')
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>([])
  const [challengeDimensions, setChallengeDimensions] = useState<string[]>(['AUTHENTICITY'])
  const [requestType, setRequestType] = useState('IMPROPER_QUESTION')
  const [targetSequence, setTargetSequence] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [automationMessage, setAutomationMessage] = useState('正在初始化自动庭审流程')
  const [streamingTurn, setStreamingTurn] = useState<StreamingTurn | null>(
    () => restoreStreamingTurn(initialSession.session_id),
  )
  const [retryAvailable, setRetryAvailable] = useState(false)
  const transcriptBody = useRef<HTMLDivElement>(null)
  const followTranscript = useRef(true)
  const transcriptScrollFrame = useRef<number | null>(null)
  const transcriptScrollTimer = useRef<number | null>(null)
  const lastTranscriptScrollAt = useRef(0)
  const autoStarted = useRef(false)
  const streamController = useRef<AbortController | null>(null)

  useEffect(() => {
    // 划词工具可能向 body 注入额外节点；庭审工作台应始终以真实视口为边界，不能被外部节点撑高。
    document.documentElement.classList.add('courtroom-active')
    document.body.classList.add('courtroom-active')
    return () => {
      document.documentElement.classList.remove('courtroom-active')
      document.body.classList.remove('courtroom-active')
    }
  }, [])

  const handleError = useCallback((caught: unknown) => {
    if (caught instanceof ApiError) setError(businessErrorLabels[caught.code] ?? `${caught.code}: ${caught.message}`)
    else setError('请求失败，请检查 API 服务。')
  }, [])

  const commitSessionData = useCallback((active: SessionView, data: Awaited<ReturnType<typeof fetchSessionData>>) => {
    setSession(active)
    setEvents(data.events)
    setEvidenceStatuses(data.evidenceStatuses)
    setEvidenceAgenda(data.evidenceAgenda)
    setRequests(data.requests)
    setStatementTraces(data.statementTraces)
    setAgentUsage(data.agentUsage)
    setAction(defaultAction(active))
    setContent('')
    setSelectedEvidence([])
    setError(null)
  }, [])

  const loadSessionData = useCallback(async (active: SessionView) => {
    const data = await fetchSessionData(active)
    commitSessionData(active, data)
  }, [commitSessionData])

  const generateReview = useCallback(async (active: SessionView) => {
    const searchResults = await Promise.all(legalQueries.map((query) => api.searchLegal(active.case_id, query)))
    const insufficient = searchResults.some((item) => item.outcome !== 'SUFFICIENT_LEGAL_AUTHORITY')
    if (insufficient) throw new ApiError('insufficient_legal_authority', '必要法源检索不足', 422)
    try {
      return await api.createReview(active.session_id, searchResults.map((item) => item.trace_id))
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'court_review_already_exists') {
        return api.getReview(active.session_id)
      }
      throw caught
    }
  }, [])

  const runAutomaticFlow = useCallback(async (start: SessionView) => {
    let active = start
    const controller = new AbortController()
    followTranscript.current = true
    streamController.current?.abort()
    streamController.current = controller
    setBusy(true)
    setError(null)
    setRetryAvailable(false)
    try {
      for (let step = 0; step < 40; step += 1) {
        setAutomationMessage('其他角色正在通过真实 LLM 生成发言')
        const storageKey = autoStepStorageKey(active.session_id)
        const idempotencyKey = sessionStorage.getItem(storageKey) ?? newIdempotencyKey()
        // 只有收到 step.completed 才清除；断线、刷新或组件重挂载会复用同一逻辑请求。
        sessionStorage.setItem(storageKey, idempotencyKey)
        let result: Awaited<ReturnType<typeof api.streamAutoStep>>
        for (let reconnectAttempt = 0; ; reconnectAttempt += 1) {
          try {
            result = await api.streamAutoStep(active.session_id, {
              onTurnStarted: ({ actor_role, participant_id }) => {
                setStreamingTurn((current) => ({
                  actorRole: actor_role,
                  participantId: participant_id,
                  text: current?.actorRole === actor_role ? current.text : '',
                  status: 'receiving',
                }))
              },
              onTurnDelta: ({ text }) => {
                setStreamingTurn((current) => current ? { ...current, text, status: 'receiving' } : current)
              },
              onTurnValidating: () => {
                setStreamingTurn((current) => current ? { ...current, status: 'validating' } : current)
                setAutomationMessage('正在校验角色权限、证据引用与输出结构')
              },
            }, controller.signal, idempotencyKey)
            break
          } catch (caught) {
            const invocationStillRunning = caught instanceof ApiError
              && caught.code === 'agent_invocation_in_progress'
            if (!invocationStillRunning || reconnectAttempt >= 19) throw caught
            setAutomationMessage('正在恢复刷新前的 Agent 发言')
            await waitForReconnect(600, controller.signal)
          }
        }
        sessionStorage.removeItem(storageKey)
        active = result.session
        await loadSessionData(active)
        setStreamingTurn(null)
        setAutomationMessage(result.message)
        if (result.status === 'failed') {
          throw new ApiError(result.error?.code ?? 'agent_failed', result.error?.message ?? result.message, 502)
        }
        if (active.phase === 'LEGAL_ANALYSIS') {
          setAutomationMessage('正在检索法律依据并生成教学复盘')
          onReview(await generateReview(active))
          return
        }
        if (active.phase === 'REVIEW' || active.phase === 'COMPLETED') {
          onReview(await api.getReview(active.session_id))
          return
        }
        if (result.status === 'waiting_for_user' || result.status === 'waiting_for_review') return
      }
      throw new ApiError('auto_step_limit_reached', '自动庭审连续步骤超过安全上限', 409)
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      // fetch 在网络断开时只抛 TypeError，也必须保留幂等键并允许用户恢复同一逻辑请求。
      const retryable = caught instanceof TypeError || (caught instanceof ApiError && [
        'agent_provider_unavailable', 'agent_provider_timeout', 'agent_provider_http_error',
        'agent_provider_incomplete', 'stream_incomplete', 'stream_step_failed',
        'agent_invocation_in_progress', 'stream_client_disconnected',
      ].includes(caught.code))
      if (!retryable) setStreamingTurn(null)
      if (retryable) setRetryAvailable(true)
      handleError(caught)
    } finally {
      if (streamController.current === controller) streamController.current = null
      setBusy(false)
    }
  }, [generateReview, handleError, loadSessionData, onReview])

  useEffect(() => {
    try {
      const key = streamingTurnStorageKey(initialSession.session_id)
      if (streamingTurn) sessionStorage.setItem(key, JSON.stringify(streamingTurn))
      else sessionStorage.removeItem(key)
    } catch {
      // 浏览器禁用会话存储时仍允许继续庭审，只是不提供刷新后的临时气泡恢复。
    }
  }, [initialSession.session_id, streamingTurn])

  useEffect(() => {
    // 四组状态使用同一个会话快照刷新，避免庭审记录和审核队列彼此错位。
    let active = true
    // 子组件运行期间阶段会持续推进，重新挂载时必须从服务端恢复最新状态，不能复用 App 的旧快照。
    void api.getSession(initialSession.session_id)
      .then(async (latestSession) => ({
        latestSession,
        data: await fetchSessionData(latestSession),
      }))
      .then(({ latestSession, data }) => {
        if (!active) return
        commitSessionData(latestSession, data)
        if (autoStart && !autoStarted.current) {
          autoStarted.current = true
          void runAutomaticFlow(latestSession)
        }
      })
      .catch(handleError)
    return () => {
      active = false
      streamController.current?.abort()
    }
  }, [autoStart, commitSessionData, handleError, initialSession, runAutomaticFlow])
  const handleTranscriptScroll = useCallback(() => {
    const panel = transcriptBody.current
    if (!panel) return
    const distanceFromBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight
    followTranscript.current = distanceFromBottom <= 80
  }, [])

  const scheduleTranscriptScroll = useCallback(() => {
    if (!followTranscript.current) return
    if (transcriptScrollFrame.current !== null || transcriptScrollTimer.current !== null) return

    const scroll = () => {
      transcriptScrollTimer.current = null
      transcriptScrollFrame.current = window.requestAnimationFrame(() => {
        transcriptScrollFrame.current = null
        if (!followTranscript.current) return
        const panel = transcriptBody.current
        if (!panel) return
        panel.scrollTop = panel.scrollHeight
        lastTranscriptScrollAt.current = Date.now()
      })
    }
    // 最多每 80ms 更新一次滚动位置，兼顾流式跟随和视觉稳定性。
    const remaining = 80 - (Date.now() - lastTranscriptScrollAt.current)
    if (remaining <= 0) scroll()
    else transcriptScrollTimer.current = window.setTimeout(scroll, remaining)
  }, [])

  useEffect(() => {
    scheduleTranscriptScroll()
  }, [events.length, scheduleTranscriptScroll, streamingTurn?.status, streamingTurn?.text])

  useEffect(() => () => {
    if (transcriptScrollTimer.current !== null) window.clearTimeout(transcriptScrollTimer.current)
    if (transcriptScrollFrame.current !== null) window.cancelAnimationFrame(transcriptScrollFrame.current)
  }, [])
  useEffect(() => {
    if (!focusedEventSequence || events.length === 0) return
    const entry = document.getElementById(`court-event-${focusedEventSequence}`)
    if (!entry) return
    if (typeof entry.scrollIntoView === 'function') {
      entry.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    entry.focus({ preventScroll: true })
  }, [events.length, focusedEventSequence])

  // 法律检索和复盘读取由专用流程编排，避免用户误触同名的底层状态机动作。
  const legalActions = useMemo(
    () => session.allowed_actions.filter((item) => !orchestratedActions.includes(item)),
    [session.allowed_actions],
  )
  const questionEvents = events.filter((item) => item.action === 'question_participant')
  const pendingRequests = requests.filter((item) => !item.resolution)
  const pendingStatements = statementTraces.filter((item) => item.new_statement && !item.review_status)
  const pendingAgendaForUser = evidenceAgenda.filter((item) => (
    item.phase === session.phase && item.responding_role === session.user_role && item.status === 'pending'
  ))
  const pendingEvidenceIds = new Set(pendingAgendaForUser.map((item) => item.evidence_id))
  const actionEvidenceStatuses = ['challenge_evidence', 'state_no_objection'].includes(action)
    ? evidenceStatuses.filter((item) => pendingEvidenceIds.has(item.evidence_id))
    : evidenceStatuses
  const submitAction = async () => {
    setBusy(true); setError(null)
    try {
      const payload: SessionActionPayload = { action }
      if (['make_statement', 'question_participant', 'challenge_evidence', 'raise_procedural_request'].includes(action)) payload.content = content.trim()
      if (action === 'question_participant') payload.target_id = targetId
      if (['submit_evidence', 'challenge_evidence', 'state_no_objection', 'make_statement'].includes(action)) payload.evidence_ids = selectedEvidence
      if (action === 'challenge_evidence') payload.challenge_dimensions = challengeDimensions
      if (action === 'raise_procedural_request') {
        payload.procedural_request_type = requestType; if (targetSequence) payload.target_event_sequence = targetSequence
      }
      const result = await api.applyAction(session.session_id, payload)
      await loadSessionData(result.session)
      setContent(''); setSelectedEvidence([])
      if (action === 'complete_phase' || action === 'question_participant') {
        await runAutomaticFlow(result.session)
      }
    } catch (caught) { handleError(caught) } finally { setBusy(false) }
  }

  const finishPhase = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.applyAction(session.session_id, { action: 'complete_phase' })
      await loadSessionData(result.session)
      await runAutomaticFlow(result.session)
    } catch (caught) {
      handleError(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="courtroom-shell">
      <section className={`workspace${casePanelCollapsed ? ' case-collapsed' : ''}${statusPanelCollapsed ? ' status-collapsed' : ''}`}>
        <aside className={`workspace-panel case-files${casePanelCollapsed ? ' collapsed' : ''}`}>
          <div className="panel-title">
            <BookOpen size={18} />
            {!casePanelCollapsed && <h2>可阅案卷</h2>}
            <button
              className="panel-toggle"
              type="button"
              aria-label={casePanelCollapsed ? '展开案卷' : '折叠案卷'}
              title={casePanelCollapsed ? '展开案卷' : '折叠案卷'}
              onClick={() => setCasePanelCollapsed((value) => !value)}
            >
              {casePanelCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
          </div>
          {!casePanelCollapsed && <nav aria-label="案卷目录">
            {([
              ['summary', '案件摘要', FileText], ['evidence', '证据目录', FileCheck2],
              ['participants', '参与人', Users], ['strategy', `${roleLabels[session.user_role]}材料`, ShieldCheck],
            ] as const).map(([id, label, Icon]) => (
              <button className={caseTab === id ? 'file-item active' : 'file-item'} key={id} onClick={() => setCaseTab(id)}><Icon size={17} /><span>{label}</span><ChevronRight size={15} /></button>
            ))}
          </nav>}
          {!casePanelCollapsed && <CasePanel active={caseTab} caseView={initialCase} statuses={evidenceStatuses} />}
        </aside>

        <section className="transcript" aria-labelledby="transcript-title">
          <div className="court-bench">
            <div className="bench-identity">
              <div className="bench-emblem"><Scale size={18} aria-hidden="true" /></div>
              <div><span>审判席</span><h2 id="transcript-title">公开庭审记录</h2></div>
            </div>
            <div className="bench-status" aria-label="当前庭审状态">
              <div><span>当前阶段</span><strong>{phaseLabels[session.phase]}</strong></div>
              <div><span>你的席位</span><strong>{roleLabels[session.user_role]}</strong></div>
              <div
                className="token-usage"
                title={`输入 ${agentUsage.input_tokens.toLocaleString('zh-CN')} · 输出 ${agentUsage.output_tokens.toLocaleString('zh-CN')} · ${agentUsage.trace_count} 次模型调用`}
              >
                <span>Token 用量</span>
                <strong>{formatTokens(agentUsage.total_tokens)}</strong>
                <small>入 {formatTokens(agentUsage.input_tokens)} · 出 {formatTokens(agentUsage.output_tokens)}</small>
              </div>
              <div><span>证据进度</span><strong>{session.submitted_evidence_ids.length} / {initialCase.evidence.length}</strong></div>
            </div>
            <div className="bench-tools">
              <span className="live-status">记录中</span>
              {reviewAvailable && onOpenReview && (
                <button className="bench-review-action" type="button" onClick={onOpenReview}>
                  <BookOpenCheck size={15} />查看教学复盘
                </button>
              )}
            </div>
            {account && <AccountControls email={account.email} onSignOut={account.onSignOut} />}
          </div>
          <div className="transcript-body" ref={transcriptBody} onScroll={handleTranscriptScroll}>
            {events.map((event) => (
              <TranscriptEntry
                actorRole={event.actor_role}
                userRole={session.user_role}
                content={eventContent(event)}
                evidenceIds={event.payload.evidence_ids}
                eventSequence={event.sequence_number}
                highlighted={event.sequence_number === focusedEventSequence}
                key={event.sequence_number}
                meta={`#${event.sequence_number} · ${phaseLabels[event.phase]}`}
              />
            ))}
            {streamingTurn && (
              <TranscriptEntry
                actorRole={streamingTurn.actorRole}
                userRole={session.user_role}
                content={streamingTurn.text || '正在接收发言内容'}
                meta={streamingTurn.status === 'validating' ? '正在校验' : '正在发言'}
                streaming
              />
            )}
          </div>
          <footer className="action-dock">
            <div className="action-topline">
              {legalActions.length > 1 && <div className="action-tabs" role="tablist" aria-label="当前合法操作">
                {legalActions.map((item) => <button key={item} className={action === item ? 'active' : ''} onClick={() => setAction(item)}>{actionLabels[item]}</button>)}
              </div>}
              <button className="ghost-action" onClick={() => { streamController.current?.abort(); onExit() }}><ArrowLeft size={16} />退出会话</button>
            </div>
            <div className="action-form">
              <ActionFields action={action} content={content} setContent={setContent} targetId={targetId} setTargetId={setTargetId} participants={initialCase.participants} statuses={actionEvidenceStatuses} selectedEvidence={selectedEvidence} setSelectedEvidence={setSelectedEvidence} challengeDimensions={challengeDimensions} setChallengeDimensions={setChallengeDimensions} requestType={requestType} setRequestType={setRequestType} targetSequence={targetSequence} setTargetSequence={setTargetSequence} questionEvents={questionEvents} />
              <div className="action-commands">
                {session.allowed_actions.includes('complete_phase') && <button className="secondary-action" disabled={busy} onClick={() => void finishPhase()}><Check size={17} />{pendingAgendaForUser.length ? `结束并暂缓 ${pendingAgendaForUser.length} 项` : '本阶段发言完毕'}</button>}
                {legalActions.length > 0 && <button className="primary-action compact" disabled={busy} onClick={() => void submitAction()}>{busy ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />}{actionLabels[action]}</button>}
              </div>
            </div>
            <div className="action-message" aria-live="polite">
              <span>{error ?? (busy ? automationMessage : `${automationMessage} · 第 ${events.length} 条记录`)}</span>
              {retryAvailable && !busy && (
                <button className="retry-action" onClick={() => void runAutomaticFlow(session)}>
                  <RotateCcw size={15} />重试本次 Agent
                </button>
              )}
            </div>
          </footer>
        </section>

        <aside className={`workspace-panel status-panel${statusPanelCollapsed ? ' collapsed' : ''}`}>
          <div className="panel-tabs" role="tablist">
            <button
              className="panel-toggle"
              type="button"
              aria-label={statusPanelCollapsed ? '展开庭审台账' : '折叠庭审台账'}
              title={statusPanelCollapsed ? '展开庭审台账' : '折叠庭审台账'}
              onClick={() => setStatusPanelCollapsed((value) => !value)}
            >
              {statusPanelCollapsed ? <PanelRightOpen size={18} /> : <PanelRightClose size={18} />}
            </button>
            {!statusPanelCollapsed && <>
            <button className={statusTab === 'evidence' ? 'active' : ''} onClick={() => setStatusTab('evidence')}>证据 {pendingAgendaForUser.length || ''}</button>
            <button className={statusTab === 'requests' ? 'active' : ''} onClick={() => setStatusTab('requests')}>请求 {pendingRequests.length || ''}</button>
            <button className={statusTab === 'statements' ? 'active' : ''} onClick={() => setStatusTab('statements')}>陈述 {pendingStatements.length || ''}</button>
            </>}
          </div>
          {!statusPanelCollapsed && <StatusPanel active={statusTab} statuses={evidenceStatuses} agenda={evidenceAgenda} requests={requests} traces={statementTraces} />}
        </aside>
      </section>
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
  active: StatusTab; statuses: EvidenceStatus[]; agenda: EvidenceAgendaItem[]; requests: ProceduralRequest[]; traces: StatementTrace[]
}

function StatusPanel({ active, statuses, agenda, requests, traces }: StatusProps) {
  if (active === 'evidence') return <div className="status-body">{statuses.map((item) => {
    const response = agenda.find((entry) => entry.evidence_id === item.evidence_id)
    const status = response?.status ?? item.status
    const labels: Record<string, string> = { not_submitted: '未提交', submitted: '已提交', pending: '待回应', challenged: '已质证', no_objection: '无异议', deferred: '暂缓' }
    return <div className="status-row" key={item.evidence_id}><span>{item.evidence_id}</span><strong>{item.title}</strong><small className={status}>{labels[status] ?? status}</small></div>
  })}</div>
  if (active === 'requests') return <div className="status-body">{requests.length === 0 ? <EmptyStatus label="暂无程序请求" /> : requests.map((item) => <div className="review-queue-item" key={item.id}><strong>{item.request_type}</strong><p>{item.content}</p><small>{item.resolution ? `已自动处理 · ${item.resolution}` : '等待系统处理'}</small></div>)}</div>
  return <div className="status-body">{traces.length === 0 ? <EmptyStatus label="暂无参与人陈述" /> : traces.map((item) => <div className="review-queue-item" key={item.id}><strong>{item.participant_id} · {item.consistency_status}</strong><p>{item.answer}</p><small>{item.review_status ?? (item.new_statement ? '等待系统审核' : item.supported_statement_ids.join('、') || '明确拒答')}</small></div>)}</div>
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
  // 普通陈述不展示证据锚点，只有明确的举证或质证动作才进入证据工作流。
  const needsEvidence = ['submit_evidence', 'challenge_evidence', 'state_no_objection'].includes(props.action)
  const evidenceOptions = props.statuses.filter((item) => props.action === 'submit_evidence' ? item.status !== 'submitted' : item.status === 'submitted')
  const toggleEvidence = (evidenceId: string) => props.setSelectedEvidence(
    props.selectedEvidence.includes(evidenceId)
      ? props.selectedEvidence.filter((item) => item !== evidenceId)
      : [...props.selectedEvidence, evidenceId],
  )
  const toggleDimension = (dimension: string) => props.setChallengeDimensions(
    props.challengeDimensions.includes(dimension)
      ? props.challengeDimensions.filter((item) => item !== dimension)
      : [...props.challengeDimensions, dimension],
  )
  if (props.action === 'advance_phase' || props.action === 'generate_legal_analysis' || props.action === 'view_review') return <div className="action-context"><Gavel size={19} /><span>{actionLabels[props.action]}</span></div>
  return <div className="field-row">
    {(props.action === 'question_participant') && (
      <div className="compact-control target-control">
        <span>询问对象</span>
        <div className="segmented-options" role="group" aria-label="询问对象">
          {props.participants.filter((item) => item.participant_type === 'witness').map((item) => (
            <button type="button" className={props.targetId === item.id ? 'selected' : ''} aria-pressed={props.targetId === item.id} onClick={() => props.setTargetId(item.id)} key={item.id}>
              {item.name}<small>{item.id}</small>
            </button>
          ))}
        </div>
      </div>
    )}
    {needsEvidence && (
      <fieldset className="evidence-picker">
        <legend>本次处理的证据 <small>已选 {props.selectedEvidence.length}</small></legend>
        <div className="evidence-options">
          {evidenceOptions.map((item) => (
            <label className={props.selectedEvidence.includes(item.evidence_id) ? 'selected' : ''} key={item.evidence_id} title={item.title}>
              <input type="checkbox" checked={props.selectedEvidence.includes(item.evidence_id)} onChange={() => toggleEvidence(item.evidence_id)} />
              <strong>{item.evidence_id}</strong><span>{item.title}</span>
            </label>
          ))}
        </div>
      </fieldset>
    )}
    {props.action === 'challenge_evidence' && (
      <div className="compact-control dimension-control">
        <span>质证维度</span>
        <div className="toggle-options">
          {['AUTHENTICITY', 'LEGALITY', 'RELEVANCE', 'PROBATIVE_VALUE'].map((item) => (
            <label className={props.challengeDimensions.includes(item) ? 'selected' : ''} key={item}>
              <input type="checkbox" checked={props.challengeDimensions.includes(item)} onChange={() => toggleDimension(item)} />{item}
            </label>
          ))}
        </div>
      </div>
    )}
    {props.action === 'raise_procedural_request' && <><label><span>请求类型</span><select value={props.requestType} onChange={(event) => props.setRequestType(event.target.value)}><option>IRRELEVANT_QUESTION</option><option>REPETITIVE_QUESTION</option><option>IMPROPER_QUESTION</option></select></label><label><span>目标发问</span><select value={props.targetSequence ?? ''} onChange={(event) => props.setTargetSequence(Number(event.target.value) || null)}><option value="">请选择</option>{props.questionEvents.map((item) => <option key={item.sequence_number} value={item.sequence_number}>#{item.sequence_number} {item.payload.content}</option>)}</select></label></>}
    {needsContent && <label className="grow"><span>内容</span><textarea value={props.content} onChange={(event) => props.setContent(event.target.value)} rows={2} /></label>}
  </div>
}
