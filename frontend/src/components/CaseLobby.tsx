import { useState } from 'react'
import { Archive, ArrowRight, Clock3, FileCog, FileText, Gavel, History, Play, RotateCcw, Scale, ShieldCheck, Users } from 'lucide-react'

import { roleLabels } from '../config'
import type { CaseSummary, SessionView, UserRole } from '../types'

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
  onManageCases: () => void
}

export function CaseLobby({ cases, sessions, loading, error, onRetry, onStart, onResume, onArchive, canManageCases, onManageCases }: Props) {
  const [role, setRole] = useState<UserRole>('prosecution')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = cases.find((item) => item.case_id === selectedId) ?? cases[0]

  return (
    <main className="lobby-shell">
      <header className="brand-bar">
        <div className="brand-lockup">
          <div className="brand"><Scale aria-hidden="true" size={22} /><span>MootCourt Lab</span></div>
          <span className="brand-divider" aria-hidden="true" />
          <span className="brand-context">刑事庭审训练工作台</span>
        </div>
        <div className="lobby-header-actions">
          <span className="environment-tag"><span className="status-dot" />训练环境</span>
        </div>
      </header>

      <div className="lobby-layout">
        <aside className="lobby-sidebar" aria-label="案件目录">
          <div className="lobby-sidebar-title"><div><p className="eyebrow">CASE DESK</p><h2>案卷工作区</h2></div><span>{cases.length} 个案件</span>{canManageCases && <button className="sidebar-admin-action" onClick={onManageCases}><FileCog size={15} />案件管理</button>}</div>
          <nav className="lobby-case-list">
            {cases.map((item) => (
              <button key={`${item.case_id}-${item.package_version}`} className={selected?.case_id === item.case_id ? 'lobby-case-link active' : 'lobby-case-link'} onClick={() => setSelectedId(item.case_id)}>
                <span className="case-link-index">{String(cases.indexOf(item) + 1).padStart(2, '0')}</span>
                <span><strong>{item.title}</strong><small>{item.case_id} · {item.package_version}</small></span>
              </button>
            ))}
          </nav>
          <div className="lobby-sidebar-note"><ShieldCheck size={17} /><span>仅限虚构案卷训练</span></div>
        </aside>

        <section className="lobby-main">
          <div className="lobby-main-heading"><div><p className="eyebrow">ACTIVE CASE</p><span>当前案件</span></div><span className="case-number">CASE / {selected?.case_id ?? '--'}</span></div>
          <section className="case-intro" aria-labelledby="case-title">
            <div className="case-heading">
              <p className="eyebrow">虚构刑事案件训练</p>
              <h1 id="case-title">{selected?.title ?? '正在读取案件'}</h1>
              <p className="case-summary">简化刑事一审训练 · 状态机推进庭审 · 可核验法源</p>
              <div className="case-meta" aria-label="案件信息"><span><Clock3 size={17} />预计 20-30 分钟</span><span><Users size={17} />用户一方 · AI 补齐其他角色</span><span><FileText size={17} />教学化简化程序</span></div>
            </div>
            <div className="case-facts" aria-label="案件版本"><div><strong>案件</strong><span>{selected?.case_id ?? '--'}</span></div><div><strong>版本</strong><span>{selected?.package_version ?? '--'}</span></div><div><strong>法域</strong><span>{selected?.jurisdiction ?? '--'}</span></div><div><strong>基准日</strong><span>{selected?.law_as_of_date ?? '--'}</span></div></div>
          </section>

          <section className="role-band" aria-labelledby="role-title">
            <div className="role-copy"><div className="role-index">01</div><div><p className="eyebrow">YOUR SEAT</p><h2 id="role-title">选择庭审席位</h2></div></div>
            <div className="role-controls"><div className="segmented" role="radiogroup" aria-label="庭审角色">{(['prosecution', 'defense'] as const).map((item) => <button className={role === item ? 'segment active' : 'segment'} key={item} onClick={() => setRole(item)} role="radio" aria-checked={role === item}>{item === 'prosecution' ? <Gavel size={19} /> : <ShieldCheck size={19} />}{roleLabels[item]}</button>)}</div><button className="primary-action" disabled={!selected || loading} onClick={() => selected && void onStart(selected, role)}>{loading ? '正在准备' : '开始庭审'} <ArrowRight size={19} /></button></div>
          </section>

          {sessions.length > 0 && <section className="session-history" aria-labelledby="session-history-title"><div className="section-heading-row"><div className="session-history-heading"><History size={18} aria-hidden="true" /><div><p className="eyebrow">CONTINUE</p><h2 id="session-history-title">最近庭审</h2></div></div><span className="section-note">从上次中断处继续</span></div><div className="session-history-list">{sessions.map((item) => <article className="session-history-item" key={item.session_id}><button className="session-resume" aria-label={`继续庭审 ${item.case_id}`} disabled={loading} onClick={() => void onResume(item)}><Play size={16} aria-hidden="true" /><span><strong>{item.case_id}</strong><small>{roleLabels[item.user_role]} · {item.phase}</small></span></button><time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time><button className="session-archive" title="归档庭审" aria-label={`归档 ${item.case_id}`} disabled={loading} onClick={() => void onArchive(item)}><Archive size={16} aria-hidden="true" /></button></article>)}</div></section>}
          {error && <div className="page-error" role="alert"><span>{error}</span><button className="retry-action" disabled={loading} onClick={onRetry}><RotateCcw size={15} />重试加载</button></div>}
        </section>
      </div>
    </main>
  )
}
