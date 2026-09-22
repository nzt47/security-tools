/**
 * 审批链会话/CSRF 客户端测试（2026-09-22 修复的回归锁）
 * ------------------------------------------------
 * 缺陷现场：工作台「审批收件箱」点批准 ⇒ `HTTP 401 · unknown_session`。
 * 根因是前端**从不开启审批会话**、也不带 CSRF 头，而裁决链要求两者齐备。
 * 本文件钉住修好的三件事：
 *   ① 没有会话标记时先开会话；已有标记时不重复开；
 *   ② 命中 `unknown_session` / `session_expired` / `csrf_mismatch` 时**重开并重试一次**
 *      （后端会话只在进程内、重启即失效 ⇒ 不自愈就是"按钮坏了"）；
 *   ③ 真实状态冲突（`session_mismatch` 等）**不重试**（重试只会再错一次并掩盖原因）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request } from './apiClient'
import {
  CSRF_COOKIE,
  approvalCsrfHeaders,
  ensureApprovalSession,
  hasApprovalSession,
  withApprovalSession,
} from './approvalChain'

vi.mock('./apiClient', async (importActual) => {
  const actual = await importActual<typeof import('./apiClient')>()
  return { ...actual, request: vi.fn() }
})

const mockRequest = vi.mocked(request)

function setCookie(name: string, value: string): void {
  document.cookie = `${name}=${value}; path=/`
}

function clearCookies(): void {
  document.cookie.split(';').forEach((c) => {
    const name = c.split('=')[0].trim()
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
  })
}

beforeEach(() => {
  clearCookies()
  mockRequest.mockReset()
  mockRequest.mockResolvedValue({ ok: true })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('审批会话的开启与复用', () => {
  it('手里没有会话标记 ⇒ 先开启审批会话', async () => {
    await ensureApprovalSession()

    expect(mockRequest).toHaveBeenCalledWith('/api/approval/session', expect.objectContaining({
      method: 'POST',
    }))
  })

  it('已有会话标记（cp_approval_csrf 可读）⇒ 不重复开启', async () => {
    setCookie(CSRF_COOKIE, 'csrf-token')

    expect(hasApprovalSession()).toBe(true)
    await ensureApprovalSession()

    expect(mockRequest).not.toHaveBeenCalled()
  })

  it('CSRF 头取自 Cookie（双重提交；没有令牌时给空对象让后端如实报错）', () => {
    expect(approvalCsrfHeaders()).toEqual({})

    setCookie(CSRF_COOKIE, 'abc123')

    expect(approvalCsrfHeaders()).toEqual({ 'X-CSRF-Token': 'abc123' })
  })
})

describe('会话失效时自愈重试（后端重启后的主路径）', () => {
  it.each(['unknown_session', 'session_expired', 'csrf_mismatch'])(
    '%s ⇒ 重开会话并重试一次（不让人看到"按钮坏了"）',
    async (code) => {
      setCookie(CSRF_COOKIE, 'stale')          // 指向一个后端已不存在的会话
      const action = vi.fn()
        .mockRejectedValueOnce(new ApiError(code, '会话不存在', 401))
        .mockResolvedValueOnce('ok')

      const out = await withApprovalSession(action)

      expect(out).toBe('ok')
      expect(action).toHaveBeenCalledTimes(2)
      expect(mockRequest).toHaveBeenCalledWith('/api/approval/session', expect.objectContaining({
        method: 'POST',
      }))
    },
  )

  it('真实状态冲突不重试（session_mismatch：链接属于另一个会话）', async () => {
    const action = vi.fn().mockRejectedValue(new ApiError('session_mismatch', '链接与会话不匹配', 403))

    await expect(withApprovalSession(action)).rejects.toThrow('链接与会话不匹配')
    expect(action).toHaveBeenCalledTimes(1)
  })

  it('第二次仍失败 ⇒ 如实抛出（不吞错、不无限重试）', async () => {
    const action = vi.fn().mockRejectedValue(new ApiError('unknown_session', '还是没有会话', 401))

    await expect(withApprovalSession(action)).rejects.toThrow('还是没有会话')
    expect(action).toHaveBeenCalledTimes(2)
  })

  it('非 ApiError（如网络错误）不重试', async () => {
    const action = vi.fn().mockRejectedValue(new Error('网络请求失败'))

    await expect(withApprovalSession(action)).rejects.toThrow('网络请求失败')
    expect(action).toHaveBeenCalledTimes(1)
  })
})
