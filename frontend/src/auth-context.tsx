import { createContext, useContext } from 'react'

export interface AccountContextValue {
  email: string
  onSignOut: () => void
}

export const AccountContext = createContext<AccountContextValue | null>(null)

export function useAccount(): AccountContextValue | null {
  return useContext(AccountContext)
}
