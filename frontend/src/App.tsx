import { useEffect, useState } from 'react'

import { api, ApiError } from './api'
import { CaseLobby } from './components/CaseLobby'
import { CourtroomPage } from './components/CourtroomPage'
import { ReviewPage } from './components/ReviewPage'
import type { CaseSummary, CaseView, CourtReview, SessionView, UserRole } from './types'

const SESSION_STORAGE_KEY = 'mootcourt.active-session-id'

function errorMessage(caught: unknown): string {
  if (caught instanceof ApiError) return `${caught.code}: ${caught.message}`
  return '无法连接庭审服务，请确认后端已经启动。'
}

export default function App() {
  const [cases, setCases] = useState<CaseSummary[]>([])
  const [caseView, setCaseView] = useState<CaseView | null>(null)
  const [session, setSession] = useState<SessionView | null>(null)
  const [review, setReview] = useState<CourtReview | null>(null)
  const [autoStartCourtroom, setAutoStartCourtroom] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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
        if (!active) return
        setSession(restoredSession)
        setCaseView(restoredCase)
      } catch (caught) {
        sessionStorage.removeItem(SESSION_STORAGE_KEY)
        if (active) setError(errorMessage(caught))
      } finally {
        if (active) setLoading(false)
      }
    }

    void bootstrap()
    return () => { active = false }
  }, [])

  const startSession = async (selectedCase: CaseSummary, role: UserRole) => {
    setLoading(true)
    setError(null)
    try {
      const [nextCase, nextSession] = await Promise.all([
        api.getCase(selectedCase.case_id, role, selectedCase.package_version),
        api.createSession(selectedCase.case_id, role, selectedCase.package_version),
      ])
      sessionStorage.setItem(SESSION_STORAGE_KEY, nextSession.session_id)
      setAutoStartCourtroom(true)
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
    setReview(null)
    setAutoStartCourtroom(true)
    setSession(null)
    setCaseView(null)
    setError(null)
  }

  if (review) {
    return <ReviewPage review={review} onBack={() => {
      // 用户明确返回庭审时禁止重新执行自动编排，否则 REVIEW 状态会立即再次打开复盘。
      setAutoStartCourtroom(false)
      setReview(null)
    }} />
  }
  if (session && caseView) {
    return <CourtroomPage
      initialCase={caseView}
      initialSession={session}
      autoStart={autoStartCourtroom}
      onExit={exitSession}
      onReview={setReview}
    />
  }
  return <CaseLobby cases={cases} loading={loading} error={error} onStart={startSession} />
}
