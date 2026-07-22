import { useEffect, useState } from 'react'

import { api, ApiError } from './api'
import { CaseLobby } from './components/CaseLobby'
import { CourtroomPage } from './components/CourtroomPage'
import { ReviewPage } from './components/ReviewPage'
import type { CaseSummary, CaseView, CourtReview, SessionView, UserRole } from './types'

const SESSION_STORAGE_KEY = 'mootcourt.active-session-id'
const SESSION_VIEW_STORAGE_KEY = 'mootcourt.active-session-view'
type SessionViewMode = 'courtroom' | 'review'

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`
  return '无法连接庭审服务，请确认后端已经启动。'
}

export default function App() {
  const [cases, setCases] = useState<CaseSummary[]>([])
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
        const availableCases = await api.listCases()
        if (!active) return
        setCases(availableCases)

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
  return <CaseLobby
    cases={cases}
    loading={loading}
    error={error}
    onRetry={() => {
      setLoading(true)
      setError(null)
      setBootstrapAttempt((attempt) => attempt + 1)
    }}
    onStart={startSession}
  />
}
