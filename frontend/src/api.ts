import type {
  AgentUsage,
  AgentTurnPayload,
  AgentTurnResponse,
  AutoStepResponse,
  CaseSummary,
  CaseImportAttempt,
  ManagedCasePackage,
  ManagedOrganization,
  CaseView,
  CourtReview,
  TurnQualityEvaluationReport,
  EvidenceStatus,
  EvidenceAgendaItem,
  ProceduralRequest,
  SessionActionPayload,
  SessionEvent,
  SessionView,
  StatementTrace,
  UserRole,
} from './types'
import { currentAccessToken } from './auth'

// 开发环境默认走 Vite 同源代理，避免访问主机名变化导致浏览器 CORS 拦截。
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const accessToken = await currentAccessToken()
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: { code?: string; message?: string }; error?: { code?: string; message?: string } }
      | null
    const detail = body?.detail ?? body?.error
    throw new ApiError(detail?.code ?? `http_${response.status}`, detail?.message ?? '请求失败', response.status)
  }
  return response.json() as Promise<T>
}

async function uploadCaseArchive(file: File): Promise<CaseImportAttempt> {
  const accessToken = await currentAccessToken()
  const response = await fetch(`${API_BASE}/admin/case-packages/imports`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/zip',
      'X-Filename': encodeURIComponent(file.name),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: file,
  })
  const body = (await response.json().catch(() => null)) as CaseImportAttempt | null
  // 422/413 仍包含可展示并已审计的导入报告，不应丢弃为普通网络错误。
  if (body?.status === 'rejected') return body
  if (!response.ok || !body) {
    throw new ApiError(`http_${response.status}`, '案件上传失败', response.status)
  }
  return body
}

export interface AutoStepStreamHandlers {
  onTurnStarted?: (payload: { actor_role: string; participant_id: string | null }) => void
  onTurnDelta?: (payload: { text: string }) => void
  onTurnValidating?: () => void
}

async function streamAutoStep(
  sessionId: string,
  handlers: AutoStepStreamHandlers,
  signal?: AbortSignal,
  idempotencyKey?: string,
): Promise<AutoStepResponse> {
  const accessToken = await currentAccessToken()
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/auto-step/stream`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
    },
    signal,
  })
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: { code?: string; message?: string } }
      | null
    throw new ApiError(
      body?.detail?.code ?? `http_${response.status}`,
      body?.detail?.message ?? '流式庭审请求失败',
      response.status,
    )
  }
  if (!response.body) throw new ApiError('stream_unavailable', '浏览器未提供流式响应体', 502)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed: AutoStepResponse | null = null

  const dispatch = (frame: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (dataLines.length === 0) return
    const payload = JSON.parse(dataLines.join('\n')) as Record<string, unknown>
    if (event === 'turn.started') {
      handlers.onTurnStarted?.(payload as { actor_role: string; participant_id: string | null })
    } else if (event === 'turn.delta') {
      handlers.onTurnDelta?.(payload as { text: string })
    } else if (event === 'turn.validating') {
      handlers.onTurnValidating?.()
    } else if (event === 'step.completed') {
      completed = payload as unknown as AutoStepResponse
    } else if (event === 'step.failed') {
      throw new ApiError(
        typeof payload.code === 'string' ? payload.code : 'stream_step_failed',
        typeof payload.message === 'string' ? payload.message : '自动庭审步骤失败',
        typeof payload.status === 'number' ? payload.status : 502,
      )
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let boundary = buffer.match(/\r?\n\r?\n/)
    while (boundary?.index !== undefined) {
      dispatch(buffer.slice(0, boundary.index))
      buffer = buffer.slice(boundary.index + boundary[0].length)
      boundary = buffer.match(/\r?\n\r?\n/)
    }
    if (done) break
  }
  if (buffer.trim()) dispatch(buffer)
  if (!completed) throw new ApiError('stream_incomplete', '流式庭审响应未正常结束', 502)
  return completed
}

export const api = {
  listAdminOrganizations: () =>
    request<ManagedOrganization[]>('/admin/case-packages/organizations'),
  listManagedCases: () => request<ManagedCasePackage[]>('/admin/case-packages'),
  uploadCaseArchive,
  publishManagedCase: (databaseId: number, organizationIds: string[]) =>
    request<ManagedCasePackage>(`/admin/case-packages/${databaseId}/publish`, {
      method: 'POST',
      body: JSON.stringify({ organization_ids: organizationIds }),
    }),
  listCases: () => request<CaseSummary[]>('/cases'),
  listSessions: (includeArchived = false) =>
    request<SessionView[]>(`/sessions?include_archived=${includeArchived}`),
  getCase: (caseId: string, role: UserRole, version?: string) =>
    request<CaseView>(`/cases/${caseId}?role=${role}${version ? `&package_version=${version}` : ''}`),
  createSession: (caseId: string, role: UserRole, packageVersion: string) =>
    request<SessionView>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ case_id: caseId, user_role: role, package_version: packageVersion }),
    }),
  getSession: (sessionId: string) => request<SessionView>(`/sessions/${sessionId}`),
  archiveSession: (sessionId: string) =>
    request<SessionView>(`/sessions/${sessionId}/archive`, { method: 'POST' }),
  getEvents: (sessionId: string) => request<SessionEvent[]>(`/sessions/${sessionId}/events`),
  getEvidenceStatuses: (sessionId: string) => request<EvidenceStatus[]>(`/sessions/${sessionId}/evidence-statuses`),
  getEvidenceAgenda: (sessionId: string) => request<EvidenceAgendaItem[]>(`/sessions/${sessionId}/evidence-agenda`),
  getProceduralRequests: (sessionId: string) => request<ProceduralRequest[]>(`/sessions/${sessionId}/procedural-requests`),
  getStatementTraces: (sessionId: string) => request<StatementTrace[]>(`/sessions/${sessionId}/participant-statement-traces`),
  getAgentUsage: (sessionId: string) => request<AgentUsage>(`/sessions/${sessionId}/usage`),
  applyAction: (sessionId: string, payload: SessionActionPayload) =>
    request<{ session: SessionView; event: SessionEvent }>(`/sessions/${sessionId}/actions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  runAgent: (sessionId: string, payload: AgentTurnPayload, idempotencyKey?: string) =>
    request<AgentTurnResponse>(`/sessions/${sessionId}/agent-turns`, {
      method: 'POST',
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
      body: JSON.stringify(payload),
    }),
  resolveRequest: (sessionId: string, requestId: string, resolution: string, reason: string) =>
    request(`/sessions/${sessionId}/procedural-requests/${requestId}/resolution`, {
      method: 'POST', body: JSON.stringify({ resolution, reason }),
    }),
  resolveStatement: (sessionId: string, traceId: string, resolution: string, reason: string) =>
    request(`/sessions/${sessionId}/participant-statement-traces/${traceId}/resolution`, {
      method: 'POST', body: JSON.stringify({ resolution, reason }),
    }),
  searchLegal: (caseId: string, query: string) =>
    request<{ trace_id: string; outcome: string }>('/legal/search', {
      method: 'POST', body: JSON.stringify({ case_id: caseId, query, top_k: 10 }),
    }),
  createReview: (sessionId: string, traceIds: string[]) =>
    request<CourtReview>(`/sessions/${sessionId}/review`, {
      method: 'POST', body: JSON.stringify({ legal_search_trace_ids: traceIds }),
    }),
  getReview: (sessionId: string) => request<CourtReview>(`/sessions/${sessionId}/review`),
  getTurnEvaluation: (sessionId: string) => request<TurnQualityEvaluationReport>(`/sessions/${sessionId}/review/turn-evaluation`),
  createTurnEvaluation: (sessionId: string, eventSequenceNumbers: number[] = []) =>
    request<TurnQualityEvaluationReport>(`/sessions/${sessionId}/review/turn-evaluation`, {
      method: 'POST', body: JSON.stringify({ event_sequence_numbers: eventSequenceNumbers }),
    }),
  autoStep: (sessionId: string) =>
    request<AutoStepResponse>(`/sessions/${sessionId}/auto-step`, { method: 'POST' }),
  streamAutoStep,
}
