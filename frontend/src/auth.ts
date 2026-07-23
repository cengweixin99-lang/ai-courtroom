import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const isSupabaseConfigured = Boolean(supabaseUrl && supabaseAnonKey)
const authStorageKey = 'mootcourt.supabase.auth'

function migrateAuthSession(): void {
  if (!isSupabaseConfigured || typeof window === 'undefined') return
  try {
    const projectRef = new URL(supabaseUrl).hostname.split('.')[0]
    const legacyKey = `sb-${projectRef}-auth-token`
    const storages = [window.localStorage, window.sessionStorage]
    const current = storages.find((storage) => storage.getItem(authStorageKey))?.getItem(authStorageKey)
    if (current) return
    const legacy = storages.find((storage) => storage.getItem(legacyKey))?.getItem(legacyKey)
    if (legacy) window.localStorage.setItem(authStorageKey, legacy)
  } catch {
    // 浏览器禁用存储时交给 Supabase 的默认错误处理，不阻断页面启动。
  }
}

migrateAuthSession()

// The browser deliberately receives only Supabase's publishable anonymous key.
// Business authorization is always evaluated again by the API against MySQL ACLs.
export const supabase: SupabaseClient | null = isSupabaseConfigured
  ? createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        // 显式固定 storage key，避免构建配置或域名变化后找不到原有会话。
        storageKey: authStorageKey,
        storage: typeof window !== 'undefined' ? window.localStorage : undefined,
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null

export async function currentAccessToken(): Promise<string | null> {
  if (!supabase) return null
  const { data, error } = await supabase.auth.getSession()
  if (error) throw error
  return data.session?.access_token ?? null
}

export async function signInWithPassword(email: string, password: string): Promise<Session> {
  if (!supabase) throw new Error('Supabase is not configured')
  const { data, error } = await supabase.auth.signInWithPassword({ email, password })
  if (error || !data.session) throw error ?? new Error('Unable to establish a session')
  return data.session
}

export async function signUpWithPassword(email: string, password: string): Promise<void> {
  if (!supabase) throw new Error('Supabase is not configured')
  const { error } = await supabase.auth.signUp({ email, password })
  if (error) throw error
}

export async function signOut(): Promise<void> {
  if (!supabase) return
  const { error } = await supabase.auth.signOut()
  if (error) throw error
}
