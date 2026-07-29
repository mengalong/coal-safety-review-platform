import { FormEvent, useState } from 'react'
import { AlertCircle, LockKeyhole, ShieldCheck, UserRound } from 'lucide-react'
import { useAuth } from '../auth'

export function LoginPage() {
  const { login } = useAuth()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    try { await login(String(form.get('login_name')), String(form.get('password'))) }
    catch (caught) { setError(caught instanceof Error ? caught.message : '登录失败') }
    finally { setBusy(false) }
  }
  return <main className="login-page"><section className="login-brand"><div className="login-lockup"><span className="login-mark"><ShieldCheck /></span><div><strong>安审智核</strong><span>煤矿安标技术文档智能审核平台</span></div></div><p>标准、规则与证据相互独立，每个审核结论都可追溯、可复核。</p></section><section className="login-panel"><form className="login-form" onSubmit={submit}><header><h1>登录审核平台</h1><p>使用分配的审核账号进入工作台</p></header>{error && <div className="form-error"><AlertCircle />{error}</div>}<label>登录名<div className="input-icon"><UserRound /><input name="login_name" autoComplete="username" required autoFocus /></div></label><label>密码<div className="input-icon"><LockKeyhole /><input name="password" type="password" autoComplete="current-password" required /></div></label><button className="btn primary" disabled={busy}>{busy ? '正在登录...' : '登录'}</button></form></section></main>
}
