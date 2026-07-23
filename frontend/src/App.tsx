import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { api, ApiError } from './api'
import {
  isSupabaseConfigured,
  signInWithPassword,
  signOut,
  signUpWithPassword,
  supabase,
} from './auth'
import { CaseLobby } from './components/CaseLobby'
import { CaseAdminPage } from './components/CaseAdminPage'
import { CourtroomPage } from './components/CourtroomPage'
import { ReviewPage } from './components/ReviewPage'
import { AccountContext } from './auth-context'
import type {
  CaseSummary,
  CaseView,
  CourtReview,
  ManagedOrganization,
  SessionView,
  UserRole,
} from './types'

const SESSION_STORAGE_KEY = 'mootcourt.active-session-id'
const SESSION_VIEW_STORAGE_KEY = 'mootcourt.active-session-view'
type SessionViewMode = 'courtroom' | 'review'

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`
  return '无法连接庭审服务，请确认后端已经启动。'
}

function CourtroomApp() {
  const [cases, setCases] = useState<CaseSummary[]>([])
  const [sessions, setSessions] = useState<SessionView[]>([])
  const [adminOrganizations, setAdminOrganizations] = useState<ManagedOrganization[]>([])
  const [showCaseAdmin, setShowCaseAdmin] = useState(false)
  const [caseView, setCaseView] = useState<CaseView | null>(null)
  const [session, setSession] = useState<SessionView | null>(null)
  const [review, setReview] = useState<CourtReview | null>(null)
  const [sessionViewMode, setSessionViewMode] = useState<SessionViewMode>('courtroom')
  const [focusedEventSequence, setFocusedEventSequence] = useState<number | null>(null)
  const [autoStartCourtroom, setAutoStartCourtroom] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0)

  useEffect(() => {
    let active = true

    const bootstrap = async () => {
      try {
        const [availableCases, availableSessions, organizations] = await Promise.all([
          api.listCases(),
          api.listSessions(),
          api.listAdminOrganizations().catch(() => []),
        ])
        if (!active) return
        setCases(availableCases)
        setSessions(availableSessions)
        setAdminOrganizations(organizations)

        const storedSessionId = sessionStorage.getItem(SESSION_STORAGE_KEY)
        if (!storedSessionId) return

        // 恢复会话时重新按锁定席位取案卷，防止把另一方私有材料带入当前上下文。
        const restoredSession = await api.getSession(storedSessionId)
        const restoredCase = await api.getCase(
          restoredSession.case_id,
          restoredSession.user_role,
          restoredSession.package_version,
        )
        let restoredReview: CourtReview | null = null
        if (restoredSession.phase === 'REVIEW' || restoredSession.phase === 'COMPLETED') {
          try {
            restoredReview = await api.getReview(restoredSession.session_id)
          } catch (caught) {
            // 复盘阶段可能正在生成报告；404 时仍允许先恢复庭审记录。
            if (!(caught instanceof ApiError) || caught.status !== 404) throw caught
          }
        }
        if (!active) return
        setSession(restoredSession)
        setCaseView(restoredCase)
        setReview(restoredReview)
        const storedView = sessionStorage.getItem(SESSION_VIEW_STORAGE_KEY)
        setSessionViewMode(
          restoredReview && storedView !== 'courtroom' ? 'review' : 'courtroom',
        )
      } catch (caught) {
        sessionStorage.removeItem(SESSION_STORAGE_KEY)
        if (active) setError(errorMessage(caught))
      } finally {
        if (active) setLoading(false)
      }
    }

    void bootstrap()
    return () => { active = false }
  }, [bootstrapAttempt])

  const startSession = async (selectedCase: CaseSummary, role: UserRole) => {
    setLoading(true)
    setError(null)
    try {
      const [nextCase, nextSession] = await Promise.all([
        api.getCase(selectedCase.case_id, role, selectedCase.package_version),
        api.createSession(selectedCase.case_id, role, selectedCase.package_version),
      ])
      sessionStorage.setItem(SESSION_STORAGE_KEY, nextSession.session_id)
      sessionStorage.setItem(SESSION_VIEW_STORAGE_KEY, 'courtroom')
      setAutoStartCourtroom(true)
      setFocusedEventSequence(null)
      setReview(null)
      setSessionViewMode('courtroom')
      setCaseView(nextCase)
      setSession(nextSession)
      setSessions((current) => [nextSession, ...current])
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setLoading(false)
    }
  }

  const exitSession = () => {
    sessionStorage.removeItem(SESSION_STORAGE_KEY)
    sessionStorage.removeItem(SESSION_VIEW_STORAGE_KEY)
    setReview(null)
    setSessionViewMode('courtroom')
    setFocusedEventSequence(null)
    setAutoStartCourtroom(true)
    setSession(null)
    setCaseView(null)
    setError(null)
    void api.listSessions().then(setSessions).catch(() => undefined)
  }

  const resumeSession = async (selectedSession: SessionView) => {
    setLoading(true)
    setError(null)
    try {
      const [restoredSession, restoredCase] = await Promise.all([
        api.getSession(selectedSession.session_id),
        api.getCase(
          selectedSession.case_id,
          selectedSession.user_role,
          selectedSession.package_version,
        ),
      ])
      sessionStorage.setItem(SESSION_STORAGE_KEY, restoredSession.session_id)
      sessionStorage.setItem(SESSION_VIEW_STORAGE_KEY, 'courtroom')
      setAutoStartCourtroom(false)
      setSessionViewMode('courtroom')
      setFocusedEventSequence(null)
      setReview(null)
      setCaseView(restoredCase)
      setSession(restoredSession)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setLoading(false)
    }
  }

  const archiveSession = async (selectedSession: SessionView) => {
    setLoading(true)
    setError(null)
    try {
      await api.archiveSession(selectedSession.session_id)
      setSessions((current) => current.filter((item) => item.session_id !== selectedSession.session_id))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setLoading(false)
    }
  }

  const openReview = (nextReview: CourtReview) => {
    setReview(nextReview)
    setSessionViewMode('review')
    sessionStorage.setItem(SESSION_VIEW_STORAGE_KEY, 'review')
  }

  if (review && sessionViewMode === 'review') {
    return <ReviewPage review={review} onBack={(eventSequence) => {
      // 用户明确返回庭审时禁止重新执行自动编排，否则 REVIEW 状态会立即再次打开复盘。
      setAutoStartCourtroom(false)
      setFocusedEventSequence(eventSequence ?? null)
      setSessionViewMode('courtroom')
      sessionStorage.setItem(SESSION_VIEW_STORAGE_KEY, 'courtroom')
    }} />
  }
  if (session && caseView) {
    return <CourtroomPage
      initialCase={caseView}
      initialSession={session}
      autoStart={autoStartCourtroom}
      focusedEventSequence={focusedEventSequence}
      onExit={exitSession}
      onReview={openReview}
      reviewAvailable={review !== null}
      onOpenReview={() => {
        setFocusedEventSequence(null)
        setSessionViewMode('review')
        sessionStorage.setItem(SESSION_VIEW_STORAGE_KEY, 'review')
      }}
    />
  }
  if (showCaseAdmin && adminOrganizations.length > 0) {
    return <CaseAdminPage
      organizations={adminOrganizations}
      onBack={() => setShowCaseAdmin(false)}
      onPublished={() => { void api.listCases().then(setCases).catch(() => undefined) }}
    />
  }
  return <CaseLobby
    cases={cases}
    sessions={sessions}
    loading={loading}
    error={error}
    onRetry={() => {
      setLoading(true)
      setError(null)
      setBootstrapAttempt((attempt) => attempt + 1)
    }}
    onStart={startSession}
    onResume={resumeSession}
    onArchive={archiveSession}
    canManageCases={adminOrganizations.length > 0}
    onManageCases={() => setShowCaseAdmin(true)}
  />
}

interface AuthGateProps {
  children: ReactNode
  authClient?: NonNullable<typeof supabase> | null
  configured?: boolean
}

export function AuthGate({ children, authClient = supabase, configured = isSupabaseConfigured }: AuthGateProps) {
  const [session, setSession] = useState<import('@supabase/supabase-js').Session | null>(null)
  const [loading, setLoading] = useState(configured)
  const [authError, setAuthError] = useState<string | null>(null)

  useEffect(() => {
    if (!authClient) return
    let active = true
    let restoring = true
    const { data } = authClient.auth.onAuthStateChange((event, nextSession) => {
      if (active) {
        // 空会话事件可能先于 storage 恢复事件到达，不能在这里提前显示登录页。
        if (nextSession) setSession(nextSession)
        if (event === 'SIGNED_OUT') {
          setSession(null)
          if (!restoring) setLoading(false)
        }
      }
    })
    void authClient.auth.getSession().then(({ data, error }) => {
      if (!active) return
      restoring = false
      if (error) setAuthError(error.message)
      setSession(data.session)
      setLoading(false)
    }).catch((caught: unknown) => {
      if (!active) return
      restoring = false
      setAuthError(caught instanceof Error ? caught.message : '无法恢复登录状态')
      setSession(null)
      setLoading(false)
    })
    return () => {
      active = false
      data.subscription.unsubscribe()
    }
  }, [authClient])

  if (!configured) return <>{children}</>
  if (loading) return <main className="auth-shell"><p>正在验证登录状态...</p></main>
  if (!session) return <AuthPage error={authError} onAuthenticated={setSession} />
  return (
    <div className="authenticated-shell">
      <AccountContext.Provider value={{ email: session.user.email ?? '', onSignOut: () => void signOut() }}>
        {children}
      </AccountContext.Provider>
    </div>
  )
}

function AuthPage({
  error,
  onAuthenticated,
}: {
  error: string | null
  onAuthenticated: (session: import('@supabase/supabase-js').Session) => void
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [registering, setRegistering] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(error)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setLocalError(null)
    setMessage(null)
    try {
      if (registering) {
        await signUpWithPassword(email, password)
        setMessage('注册请求已提交，请按 Supabase 配置完成邮箱确认。')
      } else {
        onAuthenticated(await signInWithPassword(email, password))
      }
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : '认证请求失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <p className="eyebrow">MootCourt Lab</p>
        <h1>{registering ? '创建训练账户' : '登录庭审训练'}</h1>
        <p className="auth-note">使用 Supabase 身份登录，案件权限由庭审服务校验。</p>
        <form onSubmit={(event) => void submit(event)}>
          <label>邮箱<input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>密码<input type="password" required minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {(localError || message) && <p className={localError ? 'page-error' : 'auth-success'} role="alert">{localError ?? message}</p>}
          <button className="primary-action" disabled={busy}>{busy ? '处理中...' : registering ? '注册账户' : '登录'}</button>
        </form>
        <button className="auth-switch" onClick={() => setRegistering((value) => !value)}>
          {registering ? '已有账户，返回登录' : '首次使用，创建账户'}
        </button>
      </section>
    </main>
  )
}

export default function App() {
  return <AuthGate><CourtroomApp /></AuthGate>
}
