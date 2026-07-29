import { api, onUnauthorized } from './api'
import { describe, expect, it, vi } from 'vitest'

function response(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
}

describe('api client', () => {
  it('accepts direct login responses and adds the session token', async () => {
    sessionStorage.setItem('coal_access_token', 'session-token')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() => response({ access_token: 'next-token' }))

    await expect(api<{ access_token: string }>('/auth/login', { method: 'POST' })).resolves.toEqual({ access_token: 'next-token' })
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Authorization')).toBe('Bearer session-token')
  })

  it('unwraps platform envelopes', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => response({ code: 'OK', message: 'success', data: { status: 'ready' } }))
    await expect(api('/readyz')).resolves.toEqual({ status: 'ready' })
  })

  it('invalidates the local session on unauthorized responses', async () => {
    const handler = vi.fn()
    onUnauthorized(handler)
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => response({ detail: 'session revoked' }, 401))
    await expect(api('/auth/me')).rejects.toThrow('session revoked')
    expect(handler).toHaveBeenCalledOnce()
  })
})
