import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage, ReportsPage, RulesPage, StandardsPage, TasksPage } from './pages/CatalogPages'
import { TaskWorkspace } from './pages/TaskWorkspace'
import { AdminPage } from './pages/AdminPage'
import { AppShell, Spinner } from './ui'

export default function App() {
  const { user, loading } = useAuth()
  if (loading) return <Spinner label="正在验证会话" />
  if (!user) return <LoginPage />
  return <Routes><Route element={<AppShell />}>
    <Route index element={<DashboardPage />} />
    <Route path="tasks" element={<TasksPage />} />
    <Route path="tasks/:taskId" element={<TaskWorkspace />} />
    <Route path="standards" element={<StandardsPage />} />
    <Route path="rules" element={<RulesPage />} />
    <Route path="reports" element={<ReportsPage />} />
    <Route path="admin" element={user.role === 'admin' ? <AdminPage /> : <Navigate to="/" replace />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes>
}
