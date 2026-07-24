import { Scale } from 'lucide-react'
import type { ReactNode } from 'react'

import { AccountControls } from './AccountControls'
import { useAccount } from '../auth-context'
import { WorkspaceSidebar, type WorkspaceSection } from './WorkspaceSidebar'

interface Props {
  activeSection: WorkspaceSection
  canManageCases: boolean
  loading?: boolean
  onNavigate: (section: WorkspaceSection) => void
  children: ReactNode
}

export function WorkspaceFrame({ activeSection, canManageCases, loading = false, onNavigate, children }: Props) {
  const account = useAccount()

  return (
    <main className="lobby-shell workspace-shell">
      <header className="brand-bar">
        <div className="brand-lockup">
          <div className="brand"><Scale aria-hidden="true" size={22} /><span>MootCourt Lab</span></div>
          <span className="brand-divider" aria-hidden="true" />
          <span className="brand-context">刑事庭审训练工作台</span>
        </div>
        {account && <AccountControls email={account.email} onSignOut={account.onSignOut} />}
      </header>
      <div className="workspace-layout">
        <WorkspaceSidebar activeSection={activeSection} canManageCases={canManageCases} loading={loading} onNavigate={onNavigate} />
        <section className="workspace-content">{children}</section>
      </div>
    </main>
  )
}
