import { FileCog, LayoutGrid, ShieldCheck, UsersRound } from 'lucide-react'

export type WorkspaceSection = 'training' | 'case-management' | 'organization-access'

interface Props {
  activeSection: WorkspaceSection
  caseCount: number
  canManageCases: boolean
  onNavigate: (section: WorkspaceSection) => void
}

export function WorkspaceSidebar({ activeSection, caseCount, canManageCases, onNavigate }: Props) {
  return (
    <aside className="workspace-sidebar" aria-label="工作台导航">
      <div className="workspace-sidebar-heading">
        <p>WORKSPACE</p>
        <h2>工作台</h2>
      </div>
      <nav className="workspace-nav">
        <button className={activeSection === 'training' ? 'active' : ''} type="button" onClick={() => onNavigate('training')}>
          <LayoutGrid size={17} aria-hidden="true" />
          <span>案件训练<small>{caseCount} 个案件</small></span>
        </button>
        {canManageCases && (
          <>
            <button aria-label="案件管理" className={activeSection === 'case-management' ? 'active' : ''} type="button" onClick={() => onNavigate('case-management')}>
              <FileCog size={17} aria-hidden="true" />
              <span>案件管理<small>导入与发布版本</small></span>
            </button>
            <button aria-label="组织与权限" className={activeSection === 'organization-access' ? 'active' : ''} type="button" onClick={() => onNavigate('organization-access')}>
              <UsersRound size={17} aria-hidden="true" />
              <span>组织与权限<small>成员与案件范围</small></span>
            </button>
          </>
        )}
      </nav>
      <div className="workspace-sidebar-note"><ShieldCheck size={16} aria-hidden="true" /><span>训练案卷均为虚构内容</span></div>
    </aside>
  )
}
