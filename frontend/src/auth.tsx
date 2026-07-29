import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, json, onUnauthorized } from './api'

export interface User { id: string; login_name: string; display_name: string; role: 'admin' | 'reviewer'; status: string }

interface AuthContextValue {
  user: User | null
  loading: boolean
  login: (loginName: string, password: string) => Promise<void>
  logout: () => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const clear = useCallback(() => {
    sessionStorage.removeItem('coal_access_token')
    setUser(null)
  }, [])

  useEffect(() => {
    onUnauthorized(clear)
    if (!sessionStorage.getItem('coal_access_token')) { setLoading(false); return }
    api<User>('/auth/me').then(setUser).catch(clear).finally(() => setLoading(false))
  }, [clear])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    login: async (loginName, password) => {
      const result = await api<{ access_token: string }>('/auth/login', json('POST', { login_name: loginName, password }))
      sessionStorage.setItem('coal_access_token', result.access_token)
      setUser(await api<User>('/auth/me'))
    },
    logout: async () => { try { await api('/auth/logout', json('POST')) } finally { clear() } },
    changePassword: async (currentPassword, newPassword) => {
      await api('/auth/password', json('PATCH', { current_password: currentPassword, new_password: newPassword }))
      clear()
    },
  }), [clear, loading, user])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
