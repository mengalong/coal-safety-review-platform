import { useState } from 'react'
import { AlertCircle, Bell, BookOpen, Boxes, ClipboardCheck, FileArchive, FileText, Gauge, LogOut, Menu, RefreshCw, Settings, ShieldCheck, Users, X } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from './auth'

export function Spinner({ label = '正在加载' }: { label?: string }) {
  return <div className="state"><RefreshCw className="spin" /><span>{label}</span></div>
}

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return <div className="state empty"><FileText /><strong>{title}</strong>{detail && <span>{detail}</span>}</div>
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return <div className="state error"><AlertCircle /><strong>加载失败</strong><span>{error instanceof Error ? error.message : '未知错误'}</span>{retry && <button className="btn secondary" onClick={retry}><RefreshCw />重试</button>}</div>
}

export function Status({ value }: { value?: string | null }) {
  const labels: Record<string, string> = {
    draft: '草稿', uploaded: '已上传', parse_pending: '待解析', parsing: '解析中', parsed: '已解析',
    parse_failed: '解析失败', active: '启用', disabled: '停用', obsolete: '已废止', published: '已发布',
    pending: '待执行', queued: '排队中', running: '执行中', succeeded: '通过', passed: '通过', failed: '不通过',
    unable_to_determine: '无法判断', exception: '执行异常', canceled: '已取消', open: '待复核', confirmed: '已确认',
    rejected: '已驳回', closed: '已关闭', applicable: '适用', not_applicable: '不适用', to_confirm: '待确认',
    manual_review: '人工判断', waiting_standards: '待确认标准', auditing: '审核中', awaiting_review: '待复核',
    waiting_review: '待复核', waiting_publish: '待发布', completed: '已完成', in_new_round: '整改复核',
    accepted: '已接受', reparse: '需重解析', not_required: '无需复核', executed_passed: '执行通过',
    executed_failed: '执行不通过', missing_data: '缺少数据', execution_exception: '执行异常',
  }
  const kind = ['failed', 'exception', 'parse_failed', 'executed_failed'].includes(value || '') ? 'danger'
    : ['succeeded', 'passed', 'published', 'active', 'confirmed', 'accepted', 'completed', 'executed_passed'].includes(value || '') ? 'success'
      : ['pending', 'queued', 'to_confirm', 'waiting_review', 'awaiting_review', 'unable_to_determine', 'missing_data'].includes(value || '') ? 'warning' : 'neutral'
  return <span className={`status ${kind}`}>{labels[value || ''] || value || '未知'}</span>
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description?: string; actions?: React.ReactNode }) {
  return <header className="page-head"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="actions">{actions}</div>}</header>
}

export function Modal({ title, children, close }: { title: string; children: React.ReactNode; close: () => void }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && close()}><section className="modal" role="dialog" aria-modal="true"><header><h2>{title}</h2><button className="icon-btn" title="关闭" onClick={close}><X /></button></header><div className="modal-body">{children}</div></section></div>
}

const nav = [
  ['/','工作台', Gauge], ['/tasks','审核任务', ClipboardCheck], ['/standards','标准库', BookOpen],
  ['/rules','规则与执行器', Boxes], ['/reports','报告与档案', FileArchive], ['/admin','系统管理', Settings],
] as const

export function AppShell() {
  const { user, logout } = useAuth()
  const [navigationOpen, setNavigationOpen] = useState(false)
  return <div className={`app-shell ${navigationOpen ? 'navigation-open' : ''}`}>
    <header className="global-header">
      <NavLink to="/" className="brand"><span className="brand-mark"><ShieldCheck /></span><span><strong>安审智核</strong><small>煤矿安标审核平台</small></span></NavLink>
      <button className="icon-btn mobile-menu" title={navigationOpen ? '关闭导航' : '打开导航'} aria-expanded={navigationOpen} onClick={() => setNavigationOpen(open => !open)}>{navigationOpen ? <X /> : <Menu />}</button>
      <div className="header-actions"><button className="icon-btn" title="通知"><Bell /></button><span className="avatar">{user?.display_name.slice(0, 1)}</span><span className="user-meta"><strong>{user?.display_name}</strong><small>{user?.role === 'admin' ? '系统管理员' : '审核人员'}</small></span><button className="icon-btn" title="退出登录" onClick={() => void logout()}><LogOut /></button></div>
    </header>
    <aside className="sidebar"><div className="nav-label">审核业务</div>{nav.map(([to, label, Icon]) => {
      if (to === '/admin' && user?.role !== 'admin') return null
      return <NavLink key={to} to={to} end={to === '/'} onClick={() => setNavigationOpen(false)} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><Icon /><span>{label}</span></NavLink>
    })}<div className="sidebar-foot"><ShieldCheck /><span>所有模型结论均需人工最终确认</span></div></aside>
    {navigationOpen && <button className="navigation-scrim" aria-label="关闭导航" onClick={() => setNavigationOpen(false)} />}
    <main className="main"><Outlet /></main>
  </div>
}

export const icons = { Users }
