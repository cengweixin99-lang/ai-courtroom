import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { api, ApiError } from './api'
import { ReviewPage } from './components/ReviewPage'
import type { CaseSummary, CaseView, CourtReview, SessionEvent, SessionView } from './types'

vi.mock('./api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api')>()
  return {
    ...original,
    api: {
      listCases: vi.fn(), getCase: vi.fn(), createSession: vi.fn(), getSession: vi.fn(),
      getEvents: vi.fn(), getEvidenceStatuses: vi.fn(), getEvidenceAgenda: vi.fn(), getProceduralRequests: vi.fn(),
      getStatementTraces: vi.fn(), applyAction: vi.fn(), runAgent: vi.fn(), resolveRequest: vi.fn(),
      resolveStatement: vi.fn(), searchLegal: vi.fn(), createReview: vi.fn(), getReview: vi.fn(),
      getTurnEvaluation: vi.fn(), createTurnEvaluation: vi.fn(),
      autoStep: vi.fn(), streamAutoStep: vi.fn(),
    },
  }
})

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
  mockedApi.getCase.mockResolvedValue(caseView)
  mockedApi.createSession.mockResolvedValue(session)
  mockedApi.getSession.mockResolvedValue(session)
  mockedApi.getEvents.mockResolvedValue([])
  mockedApi.getEvidenceStatuses.mockResolvedValue([])
  mockedApi.getEvidenceAgenda.mockResolvedValue([])
  mockedApi.getProceduralRequests.mockResolvedValue([])
  mockedApi.getStatementTraces.mockResolvedValue([])
  mockedApi.getTurnEvaluation.mockRejectedValue(new ApiError('turn_quality_evaluation_not_found', '不存在', 404))
  mockedApi.streamAutoStep.mockResolvedValue({
    status: 'waiting_for_user', session, event: null, message: '现在轮到你执行本方操作。', error: null,
  })
})

describe('App', () => {
  it('loads the case lobby from the API', async () => {
    render(<App />)
    expect(await screen.findByRole('heading', { name: caseSummary.title })).toBeInTheDocument()
    expect(screen.getByText('中华人民共和国')).toBeInTheDocument()
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
    expect(screen.getByText('教学复盘')).toBeInTheDocument()
    await waitFor(() => expect(mockedApi.streamAutoStep).toHaveBeenCalledTimes(1))
  })

  it('renders agent text before the streaming step completes', async () => {
    const user = userEvent.setup()
    let release: (() => void) | undefined
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
    expect(screen.getByText('正在发言')).toBeInTheDocument()
    release?.()
    const completedEntry = (await screen.findByText('公诉方完整陈述')).closest('article')
    expect(completedEntry).toHaveClass('record-entry', 'role-prosecution')
    expect(completedEntry).not.toHaveAttribute('aria-busy')
    expect(screen.queryByText('正在发言')).not.toBeInTheDocument()
  })

  it('restores a stored session with its locked role and package version', async () => {
    sessionStorage.setItem('mootcourt.active-session-id', session.session_id)
    mockedApi.getSession.mockResolvedValue(session)
    render(<App />)

    await screen.findByText('公开庭审记录')
    await waitFor(() => expect(mockedApi.getCase).toHaveBeenCalledWith('CASE-001', 'defense', '1.0.0'))
  })
})
