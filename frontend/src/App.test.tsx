import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { api, ApiError } from './api'
import { ReviewPage } from './components/ReviewPage'
import type { CaseSummary, CaseView, CourtReview, SessionView } from './types'

vi.mock('./api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api')>()
  return {
    ...original,
    api: {
      listCases: vi.fn(), getCase: vi.fn(), createSession: vi.fn(), getSession: vi.fn(),
      getEvents: vi.fn(), getEvidenceStatuses: vi.fn(), getProceduralRequests: vi.fn(),
      getStatementTraces: vi.fn(), applyAction: vi.fn(), runAgent: vi.fn(), resolveRequest: vi.fn(),
      resolveStatement: vi.fn(), searchLegal: vi.fn(), createReview: vi.fn(), getReview: vi.fn(),
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
  fact_findings: [{
    fact_id: 'F01', description: '涉案相机被取走', status: 'SUPPORTED', submitted_supporting_evidence_ids: ['E01'],
    submitted_contradicting_evidence_ids: [], appeared_statement_ids: [], challenged_evidence_ids: [],
  }],
  element_findings: [{
    element_id: 'ELEM-01', description: '盗窃公私财物', status: 'SATISFIED', supporting_fact_ids: ['F01'],
    contradicting_fact_ids: [], citations: [{ source_id: 'LAW-01', instrument_title: '中华人民共和国刑法', article_number: '第二百六十四条', text: '盗窃公私财物。', official_source_url: 'http://www.npc.gov.cn/', trace_id: 'trace-001' }],
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
  mockedApi.getEvents.mockResolvedValue([])
  mockedApi.getEvidenceStatuses.mockResolvedValue([])
  mockedApi.getProceduralRequests.mockResolvedValue([])
  mockedApi.getStatementTraces.mockResolvedValue([])
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
  })

  it('shows a stable API error when loading cases fails', async () => {
    mockedApi.listCases.mockRejectedValueOnce(new ApiError('service_unavailable', '服务不可用', 503))
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('service_unavailable: 服务不可用')
  })

  it('renders structured fact and element findings in the review', () => {
    render(<ReviewPage review={review} onBack={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '逐项事实判断' })).toBeInTheDocument()
    expect(screen.getByText('涉案相机被取走')).toBeInTheDocument()
    expect(screen.getByText('盗窃公私财物')).toBeInTheDocument()
    expect(screen.getByText('本报告不输出现实裁判结论')).toBeInTheDocument()
  })

  it('restores a stored session with its locked role and package version', async () => {
    sessionStorage.setItem('mootcourt.active-session-id', session.session_id)
    mockedApi.getSession.mockResolvedValue(session)
    render(<App />)

    await screen.findByText('公开庭审记录')
    await waitFor(() => expect(mockedApi.getCase).toHaveBeenCalledWith('CASE-001', 'defense', '1.0.0'))
  })
})
