import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Session, SupabaseClient } from '@supabase/supabase-js'

import App, { AuthGate } from './App'
import { api, ApiError } from './api'
import { CourtroomPage } from './components/CourtroomPage'
import { ReviewPage } from './components/ReviewPage'
import type { CaseSummary, CaseView, CourtReview, SessionEvent, SessionView } from './types'

vi.mock('./api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api')>()
  return {
    ...original,
    api: {
      listAdminOrganizations: vi.fn(), listManagedCases: vi.fn(), uploadCaseArchive: vi.fn(), publishManagedCase: vi.fn(),
      listOrganizationMembers: vi.fn(), setOrganizationMember: vi.fn(), removeOrganizationMember: vi.fn(),
      listCases: vi.fn(), listSessions: vi.fn(), getCase: vi.fn(), createSession: vi.fn(), getSession: vi.fn(), archiveSession: vi.fn(),
      getEvents: vi.fn(), getEvidenceStatuses: vi.fn(), getEvidenceAgenda: vi.fn(), getProceduralRequests: vi.fn(),
      getStatementTraces: vi.fn(), getAgentUsage: vi.fn(), applyAction: vi.fn(), runAgent: vi.fn(), resolveRequest: vi.fn(),
      resolveStatement: vi.fn(), searchLegal: vi.fn(), createReview: vi.fn(), getReview: vi.fn(),
      getTurnEvaluation: vi.fn(), createTurnEvaluation: vi.fn(),
      autoStep: vi.fn(), streamAutoStep: vi.fn(),
    },
  }
})

vi.mock('./auth', () => ({
  isSupabaseConfigured: false,
  supabase: null,
  currentAccessToken: vi.fn().mockResolvedValue(null),
  signInWithPassword: vi.fn(),
  signUpWithPassword: vi.fn(),
  signOut: vi.fn(),
}))

const caseSummary: CaseSummary = {
  case_id: 'CASE-001', package_version: '1.0.0', title: '青禾影像器材失窃案', status: 'ready',
  jurisdiction: '中华人民共和国', law_as_of_date: '2026-06-01',
}

const caseView: CaseView = {
  case_id: 'CASE-001', package_version: '1.0.0', role: 'defense',
  case: {
    title: caseSummary.title, summary: '一台专业相机在工作室内遗失。', charge_draft: '盗窃罪',
    law_as_of_date: '2026-06-01', estimated_duration_minutes: 25,
    disputed_issues: [{ id: 'ISSUE-01', title: '非法占有目的', description: '取走相机时的主观目的存在争议。' }],
    disclaimer: '仅供教学使用。',
  },
  facts: [], evidence: [], participants: [],
  role_materials: [{ id: 'RM-D', role: 'defense', title: '辩护策略', objectives: ['检验证据链'], priority_evidence_ids: [], known_weaknesses: [] }],
  legal_profile: { jurisdiction: '中华人民共和国', law_as_of_date: '2026-06-01' },
}

const session: SessionView = {
  session_id: 'session-001', case_id: 'CASE-001', package_version: '1.0.0', user_role: 'defense',
  phase: 'COURT_OPENING', status: 'active', turns_used: 0, allowed_actions: ['advance_phase'],
  submitted_evidence_ids: [], created_at: '2026-07-18T10:00:00Z', updated_at: '2026-07-18T10:00:00Z',
}

const review: CourtReview = {
  id: 'review-001', session_id: session.session_id, jurisdiction: '中华人民共和国', law_as_of_date: '2026-06-01',
  burden_of_proof: '公诉机关承担举证责任', standard_of_proof: '事实清楚，证据确实、充分',
  user_role: 'defense', total_score: 72,
  fact_findings: [{
    fact_id: 'F01', description: '涉案相机被取走', status: 'SUPPORTED', submitted_supporting_evidence_ids: ['E01'],
    submitted_contradicting_evidence_ids: [], appeared_statement_ids: [], challenged_evidence_ids: [],
  }],
  element_findings: [{
    element_id: 'ELEM-01', description: '盗窃公私财物', status: 'SATISFIED', supporting_fact_ids: ['F01'],
    contradicting_fact_ids: [], citations: [{ source_id: 'LAW-01', instrument_title: '中华人民共和国刑法', article_number: '第二百六十四条', text: '盗窃公私财物。', official_source_url: 'http://www.npc.gov.cn/', trace_id: 'trace-001' }],
  }],
  score_dimensions: [{
    key: 'priority_evidence_submission', label: '优先证据提交', score: 50, numerator: 1, denominator: 2,
    summary: '已提交 1/2 项本席位优先证据。',
  }],
  recommendations: [{
    id: 'missing-priority-evidence', priority: 'high', title: '补足本席位优先证据',
    detail: '尚有 1 项优先证据未提交。', related_evidence_ids: ['E02'], related_fact_ids: [], related_element_ids: [],
  }],
  turn_diagnostics: [{
    event_sequence_number: 12, actor_role: 'defense', phase: 'COURT_DEBATE_DEFENSE', action: 'make_statement',
    score: 40, evidence_ids: [], fact_ids: [],
    checks: [{ key: 'evidence_anchor', label: '绑定已提交证据', passed: false, detail: '本次陈述没有结构化证据锚点。' }],
    recommendation: '后续陈述可勾选已经提交的证据。',
  }],
  unresolved_issue_ids: ['ISSUE-01'], deterministic_conclusion_allowed: false, conclusion: null,
  disclaimer: '本报告不构成现实裁判或法律意见。',
}

const mockedApi = vi.mocked(api)

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  mockedApi.listCases.mockResolvedValue([caseSummary])
  mockedApi.listAdminOrganizations.mockResolvedValue([])
  mockedApi.listOrganizationMembers.mockResolvedValue({ organization_id: 'org-001', members: [], available_users: [] })
  mockedApi.setOrganizationMember.mockResolvedValue({ organization_id: 'org-001', members: [], available_users: [] })
  mockedApi.removeOrganizationMember.mockResolvedValue({ organization_id: 'org-001', members: [], available_users: [] })
  mockedApi.listSessions.mockResolvedValue([])
  mockedApi.getCase.mockResolvedValue(caseView)
  mockedApi.createSession.mockResolvedValue(session)
  mockedApi.getSession.mockResolvedValue(session)
  mockedApi.archiveSession.mockResolvedValue({ ...session, status: 'archived', allowed_actions: [] })
  mockedApi.getEvents.mockResolvedValue([])
  mockedApi.getEvidenceStatuses.mockResolvedValue([])
  mockedApi.getEvidenceAgenda.mockResolvedValue([])
  mockedApi.getProceduralRequests.mockResolvedValue([])
  mockedApi.getStatementTraces.mockResolvedValue([])
  mockedApi.getAgentUsage.mockResolvedValue({
    trace_count: 2, input_tokens: 1234, output_tokens: 321, total_tokens: 1555,
    latency_ms: 2400, estimated_cost_cny: 0,
  })
  mockedApi.getTurnEvaluation.mockRejectedValue(new ApiError('turn_quality_evaluation_not_found', '不存在', 404))
  mockedApi.streamAutoStep.mockResolvedValue({
    status: 'waiting_for_user', session, event: null, message: '现在轮到你执行本方操作。', error: null,
  })
})

describe('App', () => {
  it('waits for persisted authentication before deciding that a refresh is signed out', async () => {
    let resolveSession: ((value: { data: { session: Session }; error: null }) => void) | undefined
    const restoredSession = { user: { email: 'tester@example.com' } } as Session
    const authClient = {
      auth: {
        onAuthStateChange: vi.fn((callback) => {
          callback('SIGNED_OUT', null)
          return { data: { subscription: { unsubscribe: vi.fn() } } }
        }),
        getSession: vi.fn(() => new Promise((resolve) => { resolveSession = resolve })),
      },
    } as unknown as SupabaseClient

    render(<AuthGate authClient={authClient} configured><div>受保护内容</div></AuthGate>)
    expect(screen.getByText('正在验证登录状态...')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '登录庭审训练' })).not.toBeInTheDocument()

    await act(async () => resolveSession?.({ data: { session: restoredSession }, error: null }))

    expect(await screen.findByText('受保护内容')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '登录庭审训练' })).not.toBeInTheDocument()
  })

  it('loads the case lobby from the API', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: caseSummary.title })).toBeInTheDocument()
    expect(screen.getByText('中华人民共和国')).toBeInTheDocument()
  })

  it('lets an organization admin open case management and publish a draft', async () => {
    const user = userEvent.setup()
    mockedApi.listAdminOrganizations.mockResolvedValue([{
      id: 'org-001', slug: 'class-one', name: '一班',
    }])
    mockedApi.listManagedCases.mockResolvedValue([{
      database_id: 2, case_id: 'CASE-002', package_version: '1.0.0', title: '测试案件',
      content_status: 'DEVELOPMENT_READY', lifecycle_status: 'draft', jurisdiction: '中华人民共和国',
      law_as_of_date: '2026-07-01', source_filename: 'case-002.zip', source_sha256: 'a'.repeat(64),
      uploaded_by_user_id: 1, created_at: '2026-07-23T00:00:00Z', published_at: null, organization_ids: [],
    }])
    mockedApi.publishManagedCase.mockResolvedValue({
      ...(await mockedApi.listManagedCases())[0], lifecycle_status: 'published', organization_ids: ['org-001'],
    })
    render(<App />)

    await user.click(await screen.findByRole('button', { name: '案件管理' }))
    expect(await screen.findByRole('heading', { name: '案件管理' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /发布到 1 个组织/ }))

    await waitFor(() => expect(mockedApi.publishManagedCase).toHaveBeenCalledWith(2, ['org-001']))
  })

  it('resumes an owned session from the lobby', async () => {
    const user = userEvent.setup()
    mockedApi.listSessions.mockResolvedValue([session])
    render(<App />)

    await user.click(await screen.findByRole('button', { name: '最近庭审' }))
    await user.click(await screen.findByRole('button', { name: '继续庭审 CASE-001' }))

    await waitFor(() => expect(mockedApi.getSession).toHaveBeenCalledWith(session.session_id))
    expect(mockedApi.getCase).toHaveBeenCalledWith('CASE-001', 'defense', '1.0.0')
  })

  it('archives a session without deleting its history', async () => {
    const user = userEvent.setup()
    mockedApi.listSessions.mockResolvedValue([session])
    render(<App />)

    await user.click(await screen.findByRole('button', { name: '最近庭审' }))
    const archive = await screen.findByRole('button', { name: '归档 CASE-001' })
    await user.click(archive)

    await waitFor(() => expect(mockedApi.archiveSession).toHaveBeenCalledWith(session.session_id))
    expect(screen.queryByRole('button', { name: '归档 CASE-001' })).not.toBeInTheDocument()
  })

  it('creates a courtroom with the selected defense role', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))

    expect(await screen.findByText('公开庭审记录')).toBeInTheDocument()
    expect(screen.getByText('辩护方材料')).toBeInTheDocument()
    expect(mockedApi.createSession).toHaveBeenCalledWith('CASE-001', 'defense', '1.0.0')
    expect(sessionStorage.getItem('mootcourt.active-session-id')).toBe('session-001')
    expect(mockedApi.streamAutoStep).toHaveBeenCalledWith(
      'session-001', expect.any(Object), expect.any(AbortSignal), expect.any(String),
    )
    expect(screen.queryByRole('button', { name: '被告人陈述' })).not.toBeInTheDocument()
  })

  it('hides evidence selection for a plain statement and lets both side panels collapse', async () => {
    const user = userEvent.setup()
    const statementSession: SessionView = {
      ...session,
      phase: 'COURT_INVESTIGATION',
      allowed_actions: ['make_statement', 'complete_phase'],
    }
    mockedApi.getSession.mockResolvedValue(statementSession)
    mockedApi.getEvidenceStatuses.mockResolvedValue([{
      evidence_id: 'E01', title: '器材资产及盘点记录', available_to_current_role: true,
      status: 'submitted', submitted_by: 'prosecution', submitted_at: '2026-07-18T10:01:00Z',
    }])

    render(<CourtroomPage
      initialCase={{ ...caseView, evidence: [{
        id: 'E01', type: 'document', title: '器材资产及盘点记录', content: '盘点记录内容', source: '工作室',
        reliability_notes: [], related_fact_ids: [], status: 'ready',
      }] }}
      initialSession={statementSession}
      autoStart={false}
      onExit={vi.fn()}
      onReview={vi.fn()}
    />)

    const statementButton = await screen.findByRole('button', { name: '发表陈述' })
    expect(screen.getByText('Token 用量')).toBeInTheDocument()
    expect(screen.getByText('1.55k')).toBeInTheDocument()
    expect(screen.getByText('入 1.23k · 出 321')).toBeInTheDocument()
    expect(statementButton.closest('.action-dock')?.closest('.transcript')).not.toBeNull()
    expect(screen.queryByRole('tablist', { name: '当前合法操作' })).not.toBeInTheDocument()
    expect(screen.queryByText('本次处理的证据')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '折叠案卷' }))
    expect(screen.getByRole('button', { name: '展开案卷' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: '案卷目录' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '折叠庭审台账' }))
    expect(screen.getByRole('button', { name: '展开庭审台账' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '证据' })).not.toBeInTheDocument()
  })

  it('places the current user on the right and every other courtroom role on the left', async () => {
    const opposingEvents: SessionEvent[] = [
      { sequence_number: 1, phase: 'COURT_INVESTIGATION', actor_role: 'prosecution', action: 'make_statement', payload: { content: '公诉意见' }, created_at: '2026-07-18T10:01:00Z' },
      { sequence_number: 2, phase: 'COURT_INVESTIGATION', actor_role: 'defense', action: 'make_statement', payload: { content: '辩护意见' }, created_at: '2026-07-18T10:02:00Z' },
      { sequence_number: 3, phase: 'COURT_INVESTIGATION', actor_role: 'controller', action: 'advance_phase', payload: { content: '庭审控制记录' }, created_at: '2026-07-18T10:03:00Z' },
    ]
    mockedApi.getEvents.mockResolvedValue(opposingEvents)

    render(<CourtroomPage initialCase={caseView} initialSession={session} autoStart={false} onExit={vi.fn()} onReview={vi.fn()} />)

    expect((await screen.findByText('公诉意见')).closest('article')).toHaveClass('lane-left')
    expect(screen.getByText('辩护意见').closest('article')).toHaveClass('lane-right')
    expect(screen.getByText('庭审控制记录').closest('article')).toHaveClass('lane-left', 'role-controller')
  })

  it('isolates the desktop courtroom from body nodes injected by selection tools', async () => {
    const { unmount } = render(
      <CourtroomPage
        initialCase={caseView}
        initialSession={session}
        autoStart={false}
        onExit={vi.fn()}
        onReview={vi.fn()}
      />,
    )

    await screen.findByText('公开庭审记录')
    expect(document.documentElement).toHaveClass('courtroom-active')
    expect(document.body).toHaveClass('courtroom-active')

    unmount()
    expect(document.documentElement).not.toHaveClass('courtroom-active')
    expect(document.body).not.toHaveClass('courtroom-active')
  })

  it('shows a stable API error when loading cases fails', async () => {
    mockedApi.listCases.mockRejectedValueOnce(new ApiError('service_unavailable', '服务不可用', 503))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('service_unavailable: 服务不可用')

    await userEvent.setup().click(screen.getByRole('button', { name: /重试加载/ }))
    expect(await screen.findByRole('heading', { name: caseSummary.title })).toBeInTheDocument()
  })

  it('reuses the automatic-step idempotency key after an incomplete stream', async () => {
    const user = userEvent.setup()
    mockedApi.streamAutoStep
      .mockRejectedValueOnce(new ApiError('stream_incomplete', '连接提前结束', 502))
      .mockResolvedValueOnce({
        status: 'waiting_for_user', session, event: null, message: '等待用户。', error: null,
      })

    render(<App />)
    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))
    const retry = await screen.findByRole('button', { name: /重试本次 Agent/ })
    const firstKey = mockedApi.streamAutoStep.mock.calls[0]?.[3]

    await user.click(retry)
    await waitFor(() => expect(mockedApi.streamAutoStep).toHaveBeenCalledTimes(2))

    expect(firstKey).toEqual(expect.any(String))
    expect(mockedApi.streamAutoStep.mock.calls[1]?.[3]).toBe(firstKey)
  })

  it('allows an idempotent retry after the courtroom network disconnects', async () => {
    const user = userEvent.setup()
    mockedApi.streamAutoStep
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({
        status: 'waiting_for_user', session, event: null, message: '等待用户。', error: null,
      })

    render(<App />)
    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))
    expect(await screen.findByText('请求失败，请检查 API 服务。')).toBeInTheDocument()
    const retry = await screen.findByRole('button', { name: /重试本次 Agent/ })
    const firstKey = mockedApi.streamAutoStep.mock.calls[0]?.[3]

    await user.click(retry)
    await waitFor(() => expect(mockedApi.streamAutoStep).toHaveBeenCalledTimes(2))

    expect(mockedApi.streamAutoStep.mock.calls[1]?.[3]).toBe(firstKey)
  })

  it('renders structured findings and locates a diagnosed courtroom event', async () => {
    const user = userEvent.setup()
    const onBack = vi.fn()
    render(<ReviewPage review={review} onBack={onBack} />)
    expect(screen.getByRole('heading', { name: '逐项事实判断' })).toBeInTheDocument()
    expect(screen.getByText('涉案相机被取走')).toBeInTheDocument()
    expect(screen.getByText('盗窃公私财物')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '本席位庭审评分' })).toBeInTheDocument()
    expect(screen.getByText('补足本席位优先证据')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '逐发言检查' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '查看庭审记录 #12' }))
    expect(onBack).toHaveBeenCalledWith(12)
    expect(screen.getByText('本报告不输出现实裁判结论')).toBeInTheDocument()
  })

  it('highlights the diagnosed event after returning from review', async () => {
    const user = userEvent.setup()
    const reviewSession: SessionView = {
      ...session, phase: 'REVIEW', allowed_actions: ['complete_phase'], updated_at: '2026-07-18T10:05:00Z',
    }
    const diagnosedEvent: SessionEvent = {
      sequence_number: 12, phase: 'COURT_DEBATE_DEFENSE', actor_role: 'defense', action: 'make_statement',
      payload: { content: '需要改进的辩护意见' }, created_at: '2026-07-18T10:04:00Z',
    }
    mockedApi.streamAutoStep.mockResolvedValueOnce({
      status: 'progressed', session: reviewSession, event: null, message: '复盘已生成。', error: null,
    })
    mockedApi.getSession.mockResolvedValue(reviewSession)
    mockedApi.getReview.mockResolvedValue(review)
    mockedApi.getEvents.mockResolvedValue([diagnosedEvent])

    render(<App />)
    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))
    await user.click(await screen.findByRole('button', { name: '查看庭审记录 #12' }))

    const entry = (await screen.findByText('需要改进的辩护意见')).closest('article')
    expect(entry).toHaveClass('review-highlight')
    expect(entry).toHaveAttribute('data-event-sequence', '12')
  })

  it('stays in the courtroom after returning from a generated review', async () => {
    const user = userEvent.setup()
    const reviewSession: SessionView = {
      ...session,
      phase: 'REVIEW',
      allowed_actions: ['complete_phase'],
      updated_at: '2026-07-18T10:05:00Z',
    }
    mockedApi.streamAutoStep.mockResolvedValueOnce({
      status: 'progressed',
      session: reviewSession,
      event: null,
      message: '复盘已生成。',
      error: null,
    })
    mockedApi.getSession.mockResolvedValue(reviewSession)
    mockedApi.getReview.mockResolvedValue(review)

    render(<App />)
    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))

    expect(await screen.findByRole('heading', { name: '证据、事实与法律适用' })).toBeInTheDocument()
    expect(mockedApi.streamAutoStep).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: '返回庭审' }))

    expect(await screen.findByText('公开庭审记录')).toBeInTheDocument()
    const reopenReview = screen.getByRole('button', { name: '查看教学复盘' })
    expect(reopenReview).toBeInTheDocument()
    await waitFor(() => expect(mockedApi.streamAutoStep).toHaveBeenCalledTimes(1))

    await user.click(reopenReview)
    expect(await screen.findByRole('heading', { name: '证据、事实与法律适用' })).toBeInTheDocument()
    expect(mockedApi.getReview).toHaveBeenCalledTimes(1)
  })

  it('renders agent text before the streaming step completes', async () => {
    const user = userEvent.setup()
    let release: (() => void) | undefined
    let emitDelta: ((text: string) => void) | undefined
    const pending = new Promise<void>((resolve) => { release = resolve })
    const completedEvent: SessionEvent = {
      sequence_number: 2,
      phase: 'COURT_OPENING',
      actor_role: 'prosecution',
      action: 'make_statement',
      payload: { content: '公诉方完整陈述' },
      created_at: '2026-07-18T10:01:00Z',
    }
    mockedApi.getEvents.mockResolvedValueOnce([]).mockResolvedValueOnce([completedEvent])
    mockedApi.streamAutoStep.mockImplementationOnce(async (_sessionId, handlers) => {
      handlers.onTurnStarted?.({ actor_role: 'prosecution', participant_id: null })
      emitDelta = (text) => handlers.onTurnDelta?.({ text })
      handlers.onTurnDelta?.({ text: '公诉方正在形成的流式陈述' })
      await pending
      return {
        status: 'waiting_for_user', session, event: null, message: '等待用户。', error: null,
      }
    })

    render(<App />)
    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))

    const streamingEntry = (await screen.findByText('公诉方正在形成的流式陈述')).closest('article')
    expect(streamingEntry).toHaveClass('record-entry', 'role-prosecution')
    expect(streamingEntry).not.toHaveClass('streaming-entry')
    expect(streamingEntry).toHaveAttribute('aria-busy', 'true')
    const transcriptBody = streamingEntry?.closest('.transcript')?.querySelector('.transcript-body') as HTMLElement
    Object.defineProperty(transcriptBody, 'scrollHeight', { configurable: true, value: 640 })
    transcriptBody.scrollTop = 0
    await act(async () => emitDelta?.('stream grows and wraps onto more lines'))
    await waitFor(() => expect(transcriptBody.scrollTop).toBe(640))
    await waitFor(() => expect(
      sessionStorage.getItem(`mootcourt:streaming-turn:${session.session_id}`),
    ).toContain('stream grows and wraps onto more lines'))
    expect(screen.getByText('正在发言')).toBeInTheDocument()
    release?.()
    const completedEntry = (await screen.findByText('公诉方完整陈述')).closest('article')
    expect(completedEntry).toHaveClass('record-entry', 'role-prosecution')
    expect(completedEntry).not.toHaveAttribute('aria-busy')
    await waitFor(() => expect(
      sessionStorage.getItem(`mootcourt:streaming-turn:${session.session_id}`),
    ).toBeNull())
    expect(screen.queryByText('正在发言')).not.toBeInTheDocument()
  })

  it('restores the unfinished streaming bubble from session storage', async () => {
    sessionStorage.setItem(`mootcourt:auto-step:${session.session_id}`, 'restored-step-001')
    sessionStorage.setItem(`mootcourt:streaming-turn:${session.session_id}`, JSON.stringify({
      actorRole: 'prosecution', participantId: null, text: 'unfinished streamed statement', status: 'receiving',
    }))

    render(<CourtroomPage
      initialCase={caseView}
      initialSession={session}
      autoStart={false}
      onExit={vi.fn()}
      onReview={vi.fn()}
    />)

    const restored = (await screen.findByText('unfinished streamed statement')).closest('article')
    expect(restored).toHaveAttribute('aria-busy', 'true')
    expect(restored).toHaveClass('role-prosecution')
  })

  it('automatically reconnects a running idempotent Agent step', async () => {
    const user = userEvent.setup()
    mockedApi.streamAutoStep
      .mockRejectedValueOnce(new ApiError('agent_invocation_in_progress', 'still running', 409))
      .mockResolvedValueOnce({
        status: 'waiting_for_user', session, event: null, message: 'waiting for user', error: null,
      })

    render(<App />)
    await user.click(await screen.findByRole('radio', { name: '辩护方' }))
    await user.click(screen.getByRole('button', { name: /开始庭审/ }))
    await waitFor(() => expect(mockedApi.streamAutoStep).toHaveBeenCalledTimes(2), { timeout: 2_000 })

    expect(mockedApi.streamAutoStep.mock.calls[1]?.[3]).toBe(mockedApi.streamAutoStep.mock.calls[0]?.[3])
    expect(screen.queryByRole('button', { name: /重试本次 Agent/ })).not.toBeInTheDocument()
  })

  it('restores a stored session with its locked role and package version', async () => {
    sessionStorage.setItem('mootcourt.active-session-id', session.session_id)
    mockedApi.getSession.mockResolvedValue(session)
    render(<App />)

    await screen.findByText('公开庭审记录')
    await waitFor(() => expect(mockedApi.getCase).toHaveBeenCalledWith('CASE-001', 'defense', '1.0.0'))
  })

  it('restores a generated review while respecting the last selected session view', async () => {
    const reviewSession: SessionView = {
      ...session,
      phase: 'REVIEW',
      allowed_actions: ['complete_phase'],
    }
    sessionStorage.setItem('mootcourt.active-session-id', reviewSession.session_id)
    sessionStorage.setItem('mootcourt.active-session-view', 'courtroom')
    mockedApi.getSession.mockResolvedValue(reviewSession)
    mockedApi.getReview.mockResolvedValue(review)

    const { unmount } = render(<App />)
    expect(await screen.findByText('公开庭审记录')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看教学复盘' })).toBeInTheDocument()
    unmount()

    sessionStorage.setItem('mootcourt.active-session-view', 'review')
    render(<App />)
    expect(await screen.findByRole('heading', { name: '证据、事实与法律适用' })).toBeInTheDocument()
    expect(mockedApi.getReview).toHaveBeenCalledTimes(2)
  })
})
