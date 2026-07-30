import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { AuthProvider } from './auth'

function jsonResponse(data: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
}

function renderApp(path = '/') {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

describe('production application', () => {
  it('logs in through the real auth contract', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/login')) return jsonResponse({ access_token: 'token' })
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'u1', login_name: 'reviewer', display_name: '李明', role: 'reviewer', status: 'active' } })
      if (url.includes('/tasks')) return jsonResponse({ code: 'OK', data: { items: [], total: 0 } })
      if (url.includes('/issues')) return jsonResponse({ code: 'OK', data: [] })
      return jsonResponse({ code: 'OK', data: [] })
    })
    renderApp()
    fireEvent.change(screen.getByLabelText('登录名'), { target: { value: 'reviewer' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'correct-password' } })
    fireEvent.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByRole('heading', { name: '审核工作台' })).toBeInTheDocument()
    expect(sessionStorage.getItem('coal_access_token')).toBe('token')
  })

  it('redirects reviewers away from system administration', async () => {
    sessionStorage.setItem('coal_access_token', 'token')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'u1', login_name: 'reviewer', display_name: '李明', role: 'reviewer', status: 'active' } })
      if (url.includes('/tasks')) return jsonResponse({ code: 'OK', data: { items: [], total: 0 } })
      return jsonResponse({ code: 'OK', data: [] })
    })
    renderApp('/admin')
    expect(await screen.findByRole('heading', { name: '审核工作台' })).toBeInTheDocument()
    expect(screen.queryByText('系统管理')).not.toBeInTheDocument()
  })

  it('never renders stored model secrets in the admin table', async () => {
    sessionStorage.setItem('coal_access_token', 'token')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'a1', login_name: 'admin', display_name: '管理员', role: 'admin', status: 'active' } })
      if (url.endsWith('/settings/models')) return jsonResponse({ code: 'OK', data: [{ id: 'm1', provider_name: '百度千帆', provider_code: 'qianfan', base_url: 'https://qianfan.baidubce.com/v2', model_code: 'deepseek-v4-pro', model_kind: 'text', api_key_configured: true, credential_version: 2, timeout_seconds: 60, concurrency_limit: 2, status: 'active', api_key: 'must-not-render', encrypted_api_key: 'also-hidden' }] })
      return jsonResponse({ code: 'OK', data: [] })
    })
    renderApp('/admin')
    expect(await screen.findByText('deepseek-v4-pro')).toBeInTheDocument()
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
    expect(screen.queryByText('also-hidden')).not.toBeInTheDocument()
  })

  it('shows model details and a safe manual verification command', async () => {
    sessionStorage.setItem('coal_access_token', 'token')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'a1', login_name: 'admin', display_name: '管理员', role: 'admin', status: 'active' } })
      if (url.endsWith('/settings/models')) return jsonResponse({ code: 'OK', data: [{ id: 'm1', provider_name: '百度千帆', provider_code: 'qianfan', base_url: 'https://qianfan.baidubce.com/v2', model_code: 'deepseek-v4-pro', model_kind: 'text', api_key_configured: true, credential_version: 2, timeout_seconds: 60, concurrency_limit: 2, status: 'active' }] })
      return jsonResponse({ code: 'OK', data: [] })
    })
    renderApp('/admin')
    fireEvent.click(await screen.findByRole('button', { name: '查看详情' }))
    expect(await screen.findByText('https://qianfan.baidubce.com/v2/chat/completions')).toBeInTheDocument()
    expect(screen.getByText('已加密配置')).toBeInTheDocument()
    expect(screen.getByText(/QIANFAN_API_KEY/)).toBeInTheDocument()
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument()
  })

  it('shows progress and the result while testing model connectivity', async () => {
    sessionStorage.setItem('coal_access_token', 'token')
    let finishTest: (() => void) | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'a1', login_name: 'admin', display_name: '管理员', role: 'admin', status: 'active' } })
      if (url.endsWith('/settings/models')) return jsonResponse({ code: 'OK', data: [{ id: 'm1', provider_name: '百度千帆', provider_code: 'qianfan', base_url: 'https://qianfan.baidubce.com/v2', model_code: 'deepseek-v4-pro', model_kind: 'text', api_key_configured: true, credential_version: 2, timeout_seconds: 60, concurrency_limit: 2, status: 'active' }] })
      if (url.endsWith('/settings/models/m1/test')) return new Promise(resolve => { finishTest = () => resolve(new Response(JSON.stringify({ code: 'OK', data: { reachable: true, model_code: 'deepseek-v4-pro', request_id: 'req-1' } }), { status: 200, headers: { 'Content-Type': 'application/json' } })) })
      return jsonResponse({ code: 'OK', data: [] })
    })
    renderApp('/admin')
    fireEvent.click(await screen.findByRole('button', { name: '连通性测试' }))
    expect(await screen.findByRole('progressbar', { name: '模型连通性测试进度' })).toBeInTheDocument()
    finishTest?.()
    expect(await screen.findByText('连通性测试通过')).toBeInTheDocument()
    expect(screen.getByText('req-1')).toBeInTheDocument()
    expect(screen.getByText('连通正常')).toBeInTheDocument()
  })

  it('clears the session when a protected request returns 401', async () => {
    sessionStorage.setItem('coal_access_token', 'expired')
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => jsonResponse({ detail: 'session revoked' }, 401))
    renderApp('/tasks')
    await waitFor(() => expect(sessionStorage.getItem('coal_access_token')).toBeNull())
    expect(await screen.findByRole('heading', { name: '登录审核平台' })).toBeInTheDocument()
  })

  it('shows supported file formats and the parse failure reason', async () => {
    sessionStorage.setItem('coal_access_token', 'token')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'u1', login_name: 'reviewer', display_name: '审核员', role: 'reviewer', status: 'active' } })
      if (url.endsWith('/tasks/t1')) return jsonResponse({ code: 'OK', data: { id: 't1', task_no: 'TASK-1', customer_name: '测试企业', product_name: '输送机', product_model: 'DSJ120', current_round_no: 1, current_round_id: 'r1', status: 'draft', files: [{ id: 'f1', file_name: '说明书.doc', file_type: 'doc', version_no: 1, status: 'parse_failed', parse_summary: { error: { code: 'DOCUMENT_PARSE_FAILED', message: 'unsupported document type: doc' } } }] } })
      return jsonResponse({ code: 'OK', data: [] })
    })
    const view = renderApp('/tasks/t1?tab=files')
    expect(await screen.findByText(/PDF、DOCX、XLSX/)).toBeInTheDocument()
    expect(view.container.querySelector('input[name="files"]')).toHaveAttribute('accept', '.pdf,.docx,.xlsx,.xlsm,.txt,.md,.csv')
    fireEvent.mouseEnter(screen.getByText('解析失败'))
    expect(screen.getByRole('tooltip')).toHaveTextContent('不支持 DOC 文件格式，请转换为 DOCX 或 PDF 后重新上传。')
  })

  it('requires explicit confirmation before a local issue rerun', async () => {
    sessionStorage.setItem('coal_access_token', 'token')
    const requests: Array<{ url: string; options?: RequestInit }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation((input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input); requests.push({ url, options })
      if (url.endsWith('/auth/me')) return jsonResponse({ code: 'OK', data: { id: 'u1', login_name: 'reviewer', display_name: '李明', role: 'reviewer', status: 'active' } })
      if (url.endsWith('/tasks/t1')) return jsonResponse({ code: 'OK', data: { id: 't1', task_no: 'TASK-1', customer_name: '测试企业', product_name: '采煤机', product_model: 'MG-1', current_round_no: 1, current_round_id: 'r1', status: 'awaiting_review' } })
      if (url.includes('/issues?round_id=r1')) return jsonResponse({ code: 'OK', data: [{ id: 'i1', issue_code: 'ISSUE-1', title: '型号不一致', description: '客户证据与标准不一致', severity: '严重', status: 'open', system_conclusion: 'failed', evidence: [], sources: [{ rule_execution_id: 'e1' }] }] })
      if (url.endsWith('/rule-executions/e1')) return jsonResponse({ code: 'OK', data: { id: 'e1', rule_code: 'MODEL_CONSISTENCY' } })
      if (url.endsWith('/rounds/r1/audit/local-rerun')) return jsonResponse({ code: 'OK', data: { run_scope: 'local' } }, 202)
      return jsonResponse({ code: 'OK', data: [] })
    })
    renderApp('/tasks/t1?tab=issues')
    expect(await screen.findByRole('heading', { name: '型号不一致' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /局部重跑/ }))
    expect(await screen.findByRole('heading', { name: '确认局部重跑' })).toBeInTheDocument()
    expect((screen.getByDisplayValue('MODEL_CONSISTENCY') as HTMLInputElement).readOnly).toBe(true)
    fireEvent.change(screen.getByLabelText('重跑原因'), { target: { value: '补充了产品型号证据' } })
    fireEvent.click(screen.getByRole('button', { name: /确认并入队/ }))
    await waitFor(() => expect(requests.some(item => item.url.endsWith('/rounds/r1/audit/local-rerun') && item.options?.method === 'POST')).toBe(true))
    const request = requests.find(item => item.url.endsWith('/rounds/r1/audit/local-rerun'))
    expect(JSON.parse(String(request?.options?.body))).toMatchObject({ affected_rule_codes: ['MODEL_CONSISTENCY'], reason: '补充了产品型号证据' })
  })
})
