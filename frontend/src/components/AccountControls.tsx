import { LogOut, UserRound } from 'lucide-react'

interface Props {
  email: string
  onSignOut: () => void
}

function initials(email: string): string {
  return email.trim().slice(0, 1).toUpperCase() || '?'
}

export function AccountControls({ email, onSignOut }: Props) {
  return (
    <div className="account-controls" aria-label="账户操作">
      <span className="account-avatar" aria-hidden="true">
        {email ? initials(email) : <UserRound size={14} />}
      </span>
      <span className="account-email" title={email}>{email}</span>
      <button className="account-signout" type="button" aria-label="退出登录" title="退出登录" onClick={onSignOut}>
        <LogOut size={15} aria-hidden="true" />
        <span>退出登录</span>
      </button>
    </div>
  )
}
