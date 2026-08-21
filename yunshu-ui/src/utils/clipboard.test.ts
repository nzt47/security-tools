/**
 * clipboard 工具单元测试
 * - clipboard API 主路径成功
 * - API 失败降级 execCommand 成功
 * - 双路径均失败返回 false
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { copyText } from './clipboard'

beforeEach(() => {
  // jsdom 未实现 document.execCommand：先定义再按用例控制返回值
  document.execCommand = vi.fn() as unknown as typeof document.execCommand
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('copyText', () => {
  it('clipboard API 成功返回 true', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
    await expect(copyText('hello')).resolves.toBe(true)
  })

  it('clipboard API 失败时降级 execCommand，成功返回 true', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    const execSpy = vi.spyOn(document, 'execCommand').mockReturnValue(true)
    await expect(copyText('hello')).resolves.toBe(true)
    expect(execSpy).toHaveBeenCalledWith('copy')
  })

  it('双路径均失败返回 false', async () => {
    vi.stubGlobal('navigator', {
      ...navigator,
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    vi.spyOn(document, 'execCommand').mockReturnValue(false)
    await expect(copyText('hello')).resolves.toBe(false)
  })
})
