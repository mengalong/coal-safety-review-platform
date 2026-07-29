export interface ApiEnvelope<T> {
  code: string
  message: string
  data: T
  trace_id?: string
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public detail?: unknown) {
    super(message)
  }
}

let unauthorizedHandler: (() => void) | undefined

export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem('coal_access_token')
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, { ...options, headers })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接平台服务')
  }
  if (response.status === 401) unauthorizedHandler?.()
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    if (!response.ok) throw new ApiError(response.status, 'HTTP_ERROR', '服务返回异常响应')
    return response as unknown as T
  }
  const payload = await response.json()
  if (!response.ok) {
    const detail = payload.detail
    const message = typeof detail === 'string' ? detail : detail?.message || payload.message || '请求失败'
    throw new ApiError(response.status, detail?.code || payload.code || 'HTTP_ERROR', message, detail)
  }
  if (payload && typeof payload === 'object' && 'data' in payload) return (payload as ApiEnvelope<T>).data
  return payload as T
}

export function json(method: string, body?: unknown): RequestInit {
  return { method, body: body === undefined ? undefined : JSON.stringify(body) }
}
