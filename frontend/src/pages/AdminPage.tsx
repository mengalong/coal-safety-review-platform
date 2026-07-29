import { FormEvent, useState } from 'react'
import { Activity, AlertTriangle, Ban, Check, KeyRound, Play, Plus, RefreshCw, RotateCcw, ShieldCheck } from 'lucide-react'
import { api, json } from '../api'
import { useAuth } from '../auth'
import { useBusy, useRemote } from '../hooks'
import { ErrorState, Modal, PageHeader, Spinner, Status } from '../ui'

type Tab = 'models' | 'settings' | 'jobs' | 'monitoring' | 'logs' | 'security'

export function AdminPage() {
  const [tab, setTab] = useState<Tab>('models')
  return <div className="page"><PageHeader eyebrow="平台治理" title="系统管理" description="模型凭据、运行队列、监控告警和安全操作统一审计。" /><nav className="tabs">{([['models','模型配置'],['settings','业务配置'],['jobs','作业队列'],['monitoring','运行监控'],['logs','操作日志'],['security','账户安全']] as [Tab,string][]).map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}</nav>{tab === 'models' && <Models />}{tab === 'settings' && <BusinessSettings />}{tab === 'jobs' && <Jobs />}{tab === 'monitoring' && <Monitoring />}{tab === 'logs' && <Logs />}{tab === 'security' && <Security />}</div>
}

function BusinessSettings() {
  const remote = useRemote(async () => ({
    parameters: await api<any[]>('/settings/system-parameters'),
    categories: await api<any[]>('/settings/issue-categories'),
    templates: await api<any[]>('/settings/report-templates'),
  }), [])
  const [kind, setKind] = useState<'parameter' | 'category' | 'template' | null>(null)
  const busy = useBusy()
  if (remote.loading) return <Spinner label="正在加载业务配置" />
  if (remote.error || !remote.data) return <ErrorState error={remote.error} retry={remote.refresh} />
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const raw = Object.fromEntries(new FormData(event.currentTarget)); let path = '' as string; let options
    if (kind === 'parameter') { path = `/settings/system-parameters/${raw.param_key}`; options = json('PUT', { param_value: parseJson(String(raw.param_value)), status: raw.status }) }
    else if (kind === 'category') { path = '/settings/issue-categories'; options = json('POST', raw) }
    else { path = '/settings/report-templates'; options = json('POST', raw) }
    await busy.run(() => api(path, options)); setKind(null); await remote.refresh()
  }
  return <><div className="workspace-grid"><section className="section"><header className="section-head"><div><h2>系统参数</h2><p>全局运行参数以结构化 JSON 保存</p></div><button className="btn secondary" onClick={() => setKind('parameter')}><Plus />维护</button></header><div className="summary-list">{remote.data.parameters.map(item => <div key={item.param_key}><span className="mono">{item.param_key}</span><Status value={item.status} /></div>)}</div></section><section className="section"><header className="section-head"><div><h2>问题分类</h2><p>统一问题等级与默认严重度</p></div><button className="btn secondary" onClick={() => setKind('category')}><Plus />维护</button></header><div className="summary-list">{remote.data.categories.map(item => <div key={item.code}><span>{item.name}<small className="mono">{item.code}</small></span><Status value={item.status} /></div>)}</div></section></div><section className="section"><header className="section-head"><div><h2>报告模板</h2><p>报告正文模板按编码维护并纳入发布快照</p></div><button className="btn primary" onClick={() => setKind('template')}><Plus />维护模板</button></header><div className="table-wrap"><table><thead><tr><th>模板编码</th><th>模板名称</th><th>报告类型</th><th>状态</th></tr></thead><tbody>{remote.data.templates.map(item => <tr key={item.template_code}><td className="mono">{item.template_code}</td><td>{item.template_name}</td><td>{item.report_type}</td><td><Status value={item.status} /></td></tr>)}</tbody></table></div></section>{kind && <Modal title={kind === 'parameter' ? '维护系统参数' : kind === 'category' ? '维护问题分类' : '维护报告模板'} close={() => setKind(null)}><form className="form-grid" onSubmit={submit}>{kind === 'parameter' && <><label>参数键<input name="param_key" required /></label><label>状态<select name="status"><option value="active">启用</option><option value="disabled">停用</option></select></label><label className="span-2">参数值 JSON<textarea name="param_value" defaultValue="{}" required /></label></>}{kind === 'category' && <><label>分类编码<input name="code" required /></label><label>分类名称<input name="name" required /></label><label>默认严重度<select name="default_severity"><option>一般</option><option>严重</option><option>提示</option></select></label><label>状态<select name="status"><option value="active">启用</option><option value="disabled">停用</option></select></label><label className="span-2">说明<textarea name="description" /></label></>}{kind === 'template' && <><label>模板编码<input name="template_code" required /></label><label>模板名称<input name="template_name" required /></label><label>报告类型<input name="report_type" defaultValue="formal" required /></label><label>状态<select name="status"><option value="active">启用</option><option value="disabled">停用</option></select></label><label className="span-2">模板正文<textarea name="template_body" required /></label></>}{busy.error && <div className="form-error span-2">{busy.error}</div>}<footer className="form-actions span-2"><button type="button" className="btn secondary" onClick={() => setKind(null)}>取消</button><button className="btn primary" disabled={busy.busy}>保存配置</button></footer></form></Modal>}</>
}

function parseJson(value: string) {
  try { return JSON.parse(value) } catch { return value }
}

function Models() {
  const remote = useRemote(() => api<any[]>('/settings/models'), []); const [modal, setModal] = useState(false); const [rotate, setRotate] = useState<any>(null); const busy = useBusy()
  if (remote.loading) return <Spinner label="正在加载模型配置" />; if (remote.error || !remote.data) return <ErrorState error={remote.error} retry={remote.refresh} />
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const raw = Object.fromEntries(new FormData(event.currentTarget)); const values = { ...raw, timeout_seconds: Number(raw.timeout_seconds), concurrency_limit: Number(raw.concurrency_limit) }; await busy.run(() => api('/settings/models', json('POST', values))); setModal(false); await remote.refresh() }
  const rotateKey = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const apiKey = String(new FormData(event.currentTarget).get('api_key')); await busy.run(() => api(`/settings/models/${rotate.id}`, json('PATCH', { api_key: apiKey }))); setRotate(null); await remote.refresh() }
  const toggle = async (model: any) => { await busy.run(() => api(`/settings/models/${model.id}`, json('PATCH', { status: model.status === 'active' ? 'disabled' : 'active' }))); await remote.refresh() }
  const test = async (id: string) => { await busy.run(() => api(`/settings/models/${id}/test`, json('POST'))); alert('模型连通性测试成功') }
  return <><section className="section"><header className="section-head"><div><h2>统一模型网关</h2><p>接口不会返回密钥明文或密文，轮换后历史审核仍引用原凭据版本快照</p></div><button className="btn primary" onClick={() => setModal(true)}><Plus />新增模型</button></header>{busy.error && <div className="form-error">{busy.error}</div>}<div className="table-wrap"><table><thead><tr><th>供应商</th><th>模型</th><th>用途</th><th>凭据版本</th><th>超时/并发</th><th>状态</th><th>操作</th></tr></thead><tbody>{remote.data.map(model => <tr key={model.id}><td>{model.provider_name}<small className="mono">{model.provider_code}</small></td><td className="mono"><strong>{model.model_code}</strong></td><td>{model.model_kind}</td><td>V{model.credential_version}<small>{formatDate(model.key_rotated_at)}</small></td><td>{model.timeout_seconds}s / {model.concurrency_limit}</td><td><Status value={model.status} /></td><td><div className="row-actions"><button title="连通性测试" onClick={() => void test(model.id)}><Activity /></button><button title="轮换密钥" onClick={() => setRotate(model)}><KeyRound /></button><button title={model.status === 'active' ? '停用' : '启用'} onClick={() => void toggle(model)}>{model.status === 'active' ? <Ban /> : <Check />}</button></div></td></tr>)}</tbody></table></div></section>{modal && <Modal title="新增模型配置" close={() => setModal(false)}><form className="form-grid" onSubmit={submit}><label>供应商编码<input name="provider_code" defaultValue="qianfan" required /></label><label>供应商名称<input name="provider_name" defaultValue="百度千帆" required /></label><label className="span-2">API 地址<input name="base_url" defaultValue="https://qianfan.baidubce.com/v2" required /></label><label>模型编码<input name="model_code" required /></label><label>模型用途<select name="model_kind"><option value="text">文本</option><option value="multimodal">多模态</option><option value="embedding">向量</option><option value="reranker">重排序</option></select></label><label className="span-2">API Key<input name="api_key" type="password" required autoComplete="new-password" /></label><label>超时秒数<input name="timeout_seconds" type="number" defaultValue="60" min="1" /></label><label>并发限制<input name="concurrency_limit" type="number" defaultValue="2" min="1" /></label>{busy.error && <div className="form-error span-2">{busy.error}</div>}<footer className="form-actions span-2"><button type="button" className="btn secondary" onClick={() => setModal(false)}>取消</button><button className="btn primary">保存配置</button></footer></form></Modal>}{rotate && <Modal title={`轮换 ${rotate.model_code} 密钥`} close={() => setRotate(null)}><form onSubmit={rotateKey}><label>新 API Key<input name="api_key" type="password" required autoComplete="new-password" /></label><p className="notice"><ShieldCheck />新凭据将加密保存，旧凭据不会显示或写入日志。</p><footer className="form-actions"><button type="button" className="btn secondary" onClick={() => setRotate(null)}>取消</button><button className="btn primary"><RotateCcw />确认轮换</button></footer></form></Modal>}</>
}

function Jobs() {
  const remote = useRemote(() => api<any[]>('/jobs'), []); const busy = useBusy()
  if (remote.loading) return <Spinner label="正在加载作业队列" />; if (remote.error || !remote.data) return <ErrorState error={remote.error} retry={remote.refresh} />
  const action = async (id: string, name: 'run' | 'retry' | 'cancel') => { await busy.run(() => api(`/jobs/${id}/${name}`, json('POST'))); await remote.refresh() }
  return <section className="section"><header className="section-head"><div><h2>异步作业</h2><p>仅待执行作业可取消，终态作业受到状态机保护</p></div><button className="btn secondary" onClick={() => void remote.refresh()}><RefreshCw />刷新</button></header>{busy.error && <div className="form-error">{busy.error}</div>}<div className="table-wrap"><table><thead><tr><th>作业编号</th><th>类型</th><th>队列</th><th>状态</th><th>重试</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{remote.data.map(job => <tr key={job.id}><td className="mono">{job.job_code}</td><td>{job.job_type}</td><td className="mono">{job.queue_name}</td><td><Status value={job.status} /></td><td>{job.retry_count}</td><td>{formatDate(job.created_at)}</td><td><div className="row-actions">{['queued','pending'].includes(job.status) && <><button title="立即运行" onClick={() => void action(job.id, 'run')}><Play /></button><button title="取消" onClick={() => void action(job.id, 'cancel')}><Ban /></button></>}{['failed','exception'].includes(job.status) && <button title="重试" onClick={() => void action(job.id, 'retry')}><RotateCcw /></button>}</div></td></tr>)}</tbody></table></div></section>
}

function Monitoring() {
  const remote = useRemote(async () => ({ metrics: await api<any>('/monitoring'), alerts: await api<any[]>('/monitoring/alerts'), calls: await api<any[]>('/settings/model-call-logs?limit=50') }), [])
  if (remote.loading) return <Spinner label="正在读取运行监控" />; if (remote.error || !remote.data) return <ErrorState error={remote.error} retry={remote.refresh} />
  const m = remote.data.metrics
  return <><section className="metric-band compact"><div><span>等待作业</span><strong>{m.queue_waiting}</strong></div><div><span>运行作业</span><strong>{m.queue_running}</strong></div><div><span>失败作业</span><strong>{m.queue_failed}</strong></div><div><span>失败率</span><strong>{(m.job_failure_rate * 100).toFixed(1)}%</strong></div></section><div className="workspace-grid"><section className="section"><header className="section-head"><div><h2>系统告警</h2><p>队列、模型和执行器异常汇总</p></div></header>{remote.data.alerts.length ? <div className="alert-list">{remote.data.alerts.map(alert => <article key={alert.id}><AlertTriangle /><div><strong>{alert.title}</strong><p>{alert.detail}</p></div><Status value={alert.status} /></article>)}</div> : <div className="notice success"><ShieldCheck />当前无活动告警</div>}</section><section className="section"><header className="section-head"><div><h2>模型调用</h2><p>最近 50 次脱敏审计</p></div></header><div className="summary-list">{remote.data.calls.slice(0, 10).map(call => <div key={call.id}><span>{call.operation} · {call.latency_ms} ms</span><Status value={call.status} /></div>)}</div></section></div></>
}

function Logs() {
  const remote = useRemote(() => api<any[]>('/logs'), [])
  if (remote.loading) return <Spinner label="正在加载操作日志" />; if (remote.error || !remote.data) return <ErrorState error={remote.error} retry={remote.refresh} />
  return <section className="section flush"><div className="table-wrap"><table><thead><tr><th>时间</th><th>操作编码</th><th>对象类型</th><th>对象 ID</th><th>链路 ID</th><th>原因</th></tr></thead><tbody>{remote.data.map(log => <tr key={log.id}><td>{formatDate(log.created_at)}</td><td className="mono">{log.action_code}</td><td>{log.entity_type}</td><td className="mono">{log.entity_id.slice(0, 12)}</td><td className="mono">{log.trace_id || '-'}</td><td>{log.reason || '-'}</td></tr>)}</tbody></table></div></section>
}

function Security() {
  const { changePassword } = useAuth(); const busy = useBusy()
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); const next = String(form.get('new_password')); if (next !== String(form.get('confirm_password'))) { busy.setError('两次输入的新密码不一致'); return } await busy.run(() => changePassword(String(form.get('current_password')), next)) }
  return <section className="section security-panel"><header className="section-head"><div><h2>修改登录密码</h2><p>修改后撤销该用户全部有效会话，需要使用新密码重新登录</p></div><KeyRound /></header><form className="form-stack" onSubmit={submit}><label>当前密码<input name="current_password" type="password" required /></label><label>新密码<input name="new_password" type="password" minLength={10} required /></label><label>确认新密码<input name="confirm_password" type="password" minLength={10} required /></label>{busy.error && <div className="form-error">{busy.error}</div>}<button className="btn primary" disabled={busy.busy}>修改密码并退出全部会话</button></form></section>
}

function formatDate(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
