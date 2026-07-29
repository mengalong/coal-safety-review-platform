import { useCallback, useEffect, useState } from 'react'

export function useRemote<T>(loader: () => Promise<T>, dependencies: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const refresh = useCallback(() => {
    setLoading(true); setError(null)
    return loader().then(setData).catch(setError).finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies)
  useEffect(() => { void refresh() }, [refresh])
  return { data, error, loading, refresh, setData }
}

export function useBusy() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>('')
  const run = async <T,>(action: () => Promise<T>) => {
    setBusy(true); setError('')
    try { return await action() } catch (caught) { setError(caught instanceof Error ? caught.message : '操作失败'); throw caught } finally { setBusy(false) }
  }
  return { busy, error, run, setError }
}
