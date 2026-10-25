import { afterEach, describe, expect, it, vi } from 'vitest'

import { initSentry, isLoopbackDsn, shouldReport } from './sentry'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('sentry DSN 可达性门控', () => {
  it('识别 loopback DSN（本地 GlitchTip）', () => {
    expect(isLoopbackDsn('http://key@localhost:8001/1')).toBe(true)
    expect(isLoopbackDsn('http://key@127.0.0.1:8001/1')).toBe(true)
    expect(isLoopbackDsn('https://key@sentry.example.com/1')).toBe(false)
    expect(isLoopbackDsn('')).toBe(false)
    expect(isLoopbackDsn('not-a-dsn')).toBe(false)
  })

  it('未探活时放行，明确不可达才丢弃事件', () => {
    expect(shouldReport(null)).toBe(true)
    expect(shouldReport(true)).toBe(true)
    expect(shouldReport(false)).toBe(false)
  })

  it('loopback 探活用 GET 而不是 HEAD（GlitchTip 对 HEAD 回 405，控制台会多一条噪音）', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubEnv('VITE_SENTRY_DSN', 'http://public@127.0.0.1:8080/1')
    try {
      initSentry()
    } catch {
      // DSN 校验/SDK 初始化在无浏览器的测试环境里可能抛错；探活在 init 之前已发生，
      // 本用例只关心"探活打了什么样的请求"。
    }
    await Promise.resolve()
    expect(fetchMock).toHaveBeenCalled()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(String(url)).toContain('/api/0/')
    expect(init.method).toBe('GET')
  })
})
