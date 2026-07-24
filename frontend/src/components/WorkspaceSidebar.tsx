import { FileCog, History, LayoutGrid, ShieldCheck, UsersRound } from 'lucide-react'

export type WorkspaceSection = 'training' | 'recent-sessions' | 'case-management' | 'organization-access'

interface Props {
  activeSection: WorkspaceSection
  canManageCases: boolean
  loading?: boolean
  onNavigate: (section: WorkspaceSection) => void
}

export function WorkspaceSidebar({ activeSection, canManageCases, loading = false, onNavigate }: Props) {
  return (
    <aside className="workspace-sidebar" aria-label="工作台导航">

      <nav className="workspace-nav" aria-busy={loading}>
        {loading ? (
          <>
            <div className="workspace-nav-skeleton" aria-hidden="true" />
            <div className="workspace-nav-skeleton" aria-hidden="true" />
            <div className="workspace-nav-skeleton" aria-hidden="true" />
          </>
        ) : (
          <>
        <button className={activeSection === 'training' ? 'active' : ''} type="button" onClick={() => onNavigate('training')}>
          <LayoutGrid size={17} aria-hidden="true" />
          <span>案件训练</span>
        </button>
        <button aria-label="最近庭审" className={activeSection === 'recent-sessions' ? 'active' : ''} type="button" onClick={() => onNavigate('recent-sessions')}>
          <History size={17} aria-hidden="true" />
          <span>最近庭审</span>
        </button>
        {canManageCases && (
          <>
            <button aria-label="案件管理" className={activeSection === 'case-management' ? 'active' : ''} type="button" onClick={() => onNavigate('case-management')}>
              <FileCog size={17} aria-hidden="true" />
              <span>案件管理</span>
            </button>
            <button aria-label="组织与权限" className={activeSection === 'organization-access' ? 'active' : ''} type="button" onClick={() => onNavigate('organization-access')}>
              <UsersRound size={17} aria-hidden="true" />
              <span>组织与权限</span>
            </button>
          </>
        )}
          </>
        )}
      </nav>
      <div className="workspace-sidebar-note"><ShieldCheck size={16} aria-hidden="true" /><span>训练案卷均为虚构内容</span></div>
    </aside>
  )
}
