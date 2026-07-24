import { useState } from 'react'
import { Archive, ArrowRight, Gavel, History, Play, RotateCcw, Scale, ShieldCheck } from 'lucide-react'

import { roleLabels } from '../config'
import type { CaseSummary, SessionView, UserRole } from '../types'
import { AccountControls } from './AccountControls'
import { useAccount } from '../auth-context'
import { WorkspaceSidebar, type WorkspaceSection } from './WorkspaceSidebar'

interface Props {
  cases: CaseSummary[]
  sessions: SessionView[]
  loading: boolean
  error: string | null
  onRetry: () => void
  onStart: (item: CaseSummary, role: UserRole) => Promise<void>
  onResume: (session: SessionView) => Promise<void>
  onArchive: (session: SessionView) => Promise<void>
  canManageCases: boolean
  onNavigate: (section: WorkspaceSection) => void
  view?: 'training' | 'recent'
  embedded?: boolean
}

export function CaseLobby({ cases, sessions, loading, error, onRetry, onStart, onResume, onArchive, canManageCases, onNavigate, view = 'training', embedded = false }: Props) {
  const account = useAccount()
  const [role, setRole] = useState<UserRole>('prosecution')

  return (
    <main className={embedded ? 'lobby-shell embedded-workspace-content' : 'lobby-shell'}>
      {!embedded && <header className="brand-bar">
        <div className="brand-lockup">
          <div className="brand"><Scale aria-hidden="true" size={22} /><span>MootCourt Lab</span></div>
          <span className="brand-divider" aria-hidden="true" />
          <span className="brand-context">刑事庭审训练工作台</span>
        </div>
        {account && <AccountControls email={account.email} onSignOut={account.onSignOut} />}
      </header>}

      <div className="lobby-layout">
        {!embedded && <WorkspaceSidebar activeSection={view === 'recent' ? 'recent-sessions' : 'training'} canManageCases={canManageCases} onNavigate={onNavigate} />}

        <section className="lobby-main">

          {view === 'training' && <section className="case-card-grid" aria-label="案件训练">
            {cases.map((item) => (
              <article className="case-training-card" key={`${item.case_id}-${item.package_version}`}>
                <div className="case-card-info">
                  <h2>{item.title}</h2>
                  <p className="case-card-summary">{item.summary}</p>
                </div>
                <div className="case-card-foot">
                  <div className="case-card-meta">
                    <span>案件号 <strong>{item.case_id}</strong></span>
                    <span>版本 <strong>{item.package_version}</strong></span>
                    <span>法域 <strong>{item.jurisdiction}</strong></span>
                    <span>法律基准 <strong>{item.law_as_of_date}</strong></span>
                  </div>
                  <div className="case-card-actions">
                    <div className="segmented" role="radiogroup" aria-label="庭审角色">{(['prosecution', 'defense'] as const).map((seat) => <button className={role === seat ? 'segment active' : 'segment'} key={seat} onClick={() => setRole(seat)} role="radio" aria-checked={role === seat}>{seat === 'prosecution' ? <Gavel size={15} /> : <ShieldCheck size={15} />}{roleLabels[seat]}</button>)}</div>
                    <button className="primary-action compact" disabled={loading} onClick={() => void onStart(item, role)}>{loading ? '正在准备' : '开始庭审'} <ArrowRight size={16} /></button>
                  </div>
                </div>
              </article>
            ))}
          </section>}

          {view === 'recent' && <section className="session-history recent-sessions-page" aria-labelledby="session-history-title"><div className="section-heading-row"><div className="session-history-heading"><History size={18} aria-hidden="true" /><div><p className="eyebrow">CONTINUE</p><h2 id="session-history-title">继续训练</h2></div></div></div>{sessions.length > 0 ? <div className="session-history-list">{sessions.map((item) => <article className="session-history-item" key={item.session_id}><button className="session-resume" aria-label={`继续庭审 ${item.case_id}`} disabled={loading} onClick={() => void onResume(item)}><Play size={16} aria-hidden="true" /><span><strong>{item.case_id}</strong><small>{roleLabels[item.user_role]} · {item.phase}</small></span></button><time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time><button className="session-archive" title="归档庭审" aria-label={`归档 ${item.case_id}`} disabled={loading} onClick={() => void onArchive(item)}><Archive size={16} aria-hidden="true" /></button></article>)}</div> : <p className="recent-empty">暂无最近庭审记录</p>}</section>}
          {error && <div className="page-error" role="alert"><span>{error}</span><button className="retry-action" disabled={loading} onClick={onRetry}><RotateCcw size={15} />重试加载</button></div>}
        </section>
      </div>
    </main>
  )
}
