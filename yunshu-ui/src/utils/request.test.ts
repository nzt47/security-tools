/**
 * request.ts 增强单元测试：createRequestAbort / requestWithRetry
 * - createRequestAbort：cancel 后 signal.aborted 为 true
 * - requestWithRetry：默认关闭仅一次 / 启用后 GET 网络错误重试 / 4xx 不重试 / 非 GET 不重试
 * 说明：mock axios.create 返回受控实例，拦截器 use 为空实现（不触发真实 toast/401 逻辑）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { requestWithRetry, createRequestAbort } from './request'

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }))

vi.mock('axios', async (importOriginal) => {
  const actual = await importOriginal<typeof import('axios')>()
  return {
    ...actual,
    default: {
      ...actual.default,
      create: vi.fn(() => ({
        request: requestMock,
        interceptors: {
          request: { use: vi.fn() },
          response: { use: vi.fn() },
        },
      })),
    },
  }
})

beforeEach(() => {
  requestMock.mockReset()
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('createRequestAbort', () => {
  it('cancel 后 signal.aborted 为 true', () => {
    const { signal, cancel } = createRequestAbort()
    expect(signal.aborted).toBe(false)
    cancel()
    expect(signal.aborted).toBe(true)
  })
})

describe('requestWithRetry', () => {
  it('默认关闭（VITE_REQUEST_RETRY_ENABLED=0）：仅请求一次，不重试', async () => {
    vi.stubEnv('VITE_REQUEST_RETRY_ENABLED', '0')
    requestMock.mockRejectedValueOnce(new TypeError('net'))
    await expect(requestWithRetry({ url: '/x', method: 'GET' })).rejects.toThrow('net')
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('启用重试：GET 网络错误重试 retries 次后成功', async () => {
    vi.stubEnv('VITE_REQUEST_RETRY_ENABLED', 'true')
    requestMock
      .mockRejectedValueOnce(new TypeError('net'))
      .mockRejectedValueOnce(new TypeError('net'))
      .mockResolvedValueOnce({ ok: 1 })

    const result = await requestWithRetry({ url: '/x', method: 'GET' }, { retries: 2, retryDelayMs: 1 })
    expect(result).toEqual({ ok: 1 })
    expect(requestMock).toHaveBeenCalledTimes(3)
  })

  it('4xx 业务错误不重试（如 401 交拦截器登出）', async () => {
    vi.stubEnv('VITE_REQUEST_RETRY_ENABLED', 'true')
    const err = new Error('unauth') as Error & { response?: { status: number } }
    err.response = { status: 401 }
    requestMock.mockRejectedValueOnce(err)

    await expect(requestWithRetry({ url: '/x', method: 'GET' })).rejects.toBe(err)
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('非幂等请求（POST）不重试', async () => {
    vi.stubEnv('VITE_REQUEST_RETRY_ENABLED', 'true')
    requestMock.mockRejectedValueOnce(new TypeError('net'))
    await expect(requestWithRetry({ url: '/x', method: 'POST' })).rejects.toThrow('net')
    expect(requestMock).toHaveBeenCalledTimes(1)
  })
})
