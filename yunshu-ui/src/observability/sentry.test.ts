import { describe, expect, it } from 'vitest'

import { isLoopbackDsn, shouldReport } from './sentry'

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
})
