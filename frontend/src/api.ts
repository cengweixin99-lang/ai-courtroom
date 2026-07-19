import type {
  AgentTurnPayload,
  AgentTurnResponse,
  CaseSummary,
  CaseView,
  CourtReview,
  EvidenceStatus,
  ProceduralRequest,
  SessionActionPayload,
  SessionEvent,
  SessionView,
  StatementTrace,
  UserRole,
} from './types'

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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
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

export const api = {
  listCases: () => request<CaseSummary[]>('/cases'),
  getCase: (caseId: string, role: UserRole, version?: string) =>
    request<CaseView>(`/cases/${caseId}?role=${role}${version ? `&package_version=${version}` : ''}`),
  createSession: (caseId: string, role: UserRole, packageVersion: string) =>
    request<SessionView>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ case_id: caseId, user_role: role, package_version: packageVersion }),
    }),
  getSession: (sessionId: string) => request<SessionView>(`/sessions/${sessionId}`),
  getEvents: (sessionId: string) => request<SessionEvent[]>(`/sessions/${sessionId}/events`),
  getEvidenceStatuses: (sessionId: string) => request<EvidenceStatus[]>(`/sessions/${sessionId}/evidence-statuses`),
  getProceduralRequests: (sessionId: string) => request<ProceduralRequest[]>(`/sessions/${sessionId}/procedural-requests`),
  getStatementTraces: (sessionId: string) => request<StatementTrace[]>(`/sessions/${sessionId}/participant-statement-traces`),
  applyAction: (sessionId: string, payload: SessionActionPayload) =>
    request<{ session: SessionView; event: SessionEvent }>(`/sessions/${sessionId}/actions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  runAgent: (sessionId: string, payload: AgentTurnPayload) =>
    request<AgentTurnResponse>(`/sessions/${sessionId}/agent-turns`, {
      method: 'POST',
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
}
