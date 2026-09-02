/**
 * SSR 兼容性加固 —— 自动化回归测试
 * ------------------------------------------------
 * 覆盖对象：src/utils/storage.ts 的 SSR 防线
 *   - safeGetLocalStorage：渲染期安全读取（window 守卫，SSR 回退 null）
 *   - getRaw：try/catch 兜底（隐私模式 / localStorage 访问异常不抛致命异常）
 *
 * 回归意义：未来改动若移除 window 守卫或 try/catch，此测试立即失败，
 * 防止 SSR 兼容性退化（服务端渲染崩溃 / hydration 不一致）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getRaw, safeGetLocalStorage } from './storage'

describe('SSR 兼容性防线', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('CSR：window 存在时 safeGetLocalStorage 正常读取 localStorage', () => {
    localStorage.setItem('ssr-test:key', 'v1')
    expect(safeGetLocalStorage('ssr-test:key')).toBe('v1')
    localStorage.removeItem('ssr-test:key')
  })

  it('SSR：window 不存在（typeof window === undefined）时返回 null 且不抛错', () => {
    // 模拟服务端渲染环境：jsdom 中 window 被置为 undefined
    vi.stubGlobal('window', undefined)
    expect(safeGetLocalStorage('ssr-test:any')).toBeNull()
    // 无论读取哪个键都不应抛出 ReferenceError
    expect(() => safeGetLocalStorage('ssr-test:any')).not.toThrow()
  })

  it('隐私模式：localStorage 访问抛错时 getRaw 兜底返回 null 且不抛错', () => {
    const original = localStorage.getItem
    localStorage.getItem = vi.fn(() => {
      throw new DOMException('The operation is insecure', 'SecurityError')
    })
    try {
      expect(getRaw('ssr-test:key')).toBeNull()
    } finally {
      localStorage.getItem = original
    }
  })
})
