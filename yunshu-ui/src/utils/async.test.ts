/**
 * async 工具单元测试
 * - debounce：延迟执行、连续调用重置、cancel 阻止执行
 * - throttle：leading 立即执行、间隔内合并、间隔后恢复、cancel
 * - isAbortError：仅识别 DOMException AbortError
 * - abortable：正常 resolve / 中止 reject AbortError / 已中止立即拒绝
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { debounce, throttle, isAbortError, abortable } from './async'

afterEach(() => {
  vi.useRealTimers()
})

describe('debounce', () => {
  it('延迟后执行，连续调用只执行最后一次', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const d = debounce(fn, 300)
    d('a')
    d('b')
    d('c')
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(299)
    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('c')
  })

  it('cancel 阻止未执行的调用', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const d = debounce(fn, 300)
    d('a')
    d.cancel()
    vi.advanceTimersByTime(1000)
    expect(fn).not.toHaveBeenCalled()
  })
})

describe('throttle', () => {
  it('leading 立即执行；间隔内调用合并为一次 trailing；窗口结束后再执行', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const t = throttle(fn, 1000)
    t('a') // T0 → 立即执行（leading）
    expect(fn).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(100)
    t('b') // T0+100，间隔内 → 合并，安排 trailing
    expect(fn).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(900) // T0+1000 → trailing 执行 'b'，窗口重置
    expect(fn).toHaveBeenCalledTimes(2)
    expect(fn).toHaveBeenLastCalledWith('b')
    t('c') // T0+1000，距上次执行 0ms → 仍被节流，安排下一次 trailing
    vi.advanceTimersByTime(1000) // T0+2000 → trailing 执行 'c'
    expect(fn).toHaveBeenCalledTimes(3)
    expect(fn).toHaveBeenLastCalledWith('c')
  })

  it('cancel 挂起中的 trailing 调用', () => {
    vi.useFakeTimers()
    const fn = vi.fn()
    const t = throttle(fn, 1000)
    t('a')
    vi.advanceTimersByTime(100)
    t('b')
    t.cancel()
    vi.advanceTimersByTime(2000)
    expect(fn).toHaveBeenCalledTimes(1)
  })
})

describe('isAbortError', () => {
  it('识别 DOMException AbortError', () => {
    expect(isAbortError(new DOMException('aborted', 'AbortError'))).toBe(true)
  })

  it('普通 Error 与空值返回 false', () => {
    expect(isAbortError(new Error('boom'))).toBe(false)
    expect(isAbortError(undefined)).toBe(false)
    expect(isAbortError('abort')).toBe(false)
  })
})

describe('abortable', () => {
  it('无 signal / 未中止时正常 resolve', async () => {
    await expect(abortable(Promise.resolve(1))).resolves.toBe(1)
    const controller = new AbortController()
    await expect(abortable(Promise.resolve(2), controller.signal)).resolves.toBe(2)
  })

  it('signal 中止时拒绝为 AbortError', async () => {
    const controller = new AbortController()
    const pending = new Promise<string>((resolve) => setTimeout(() => resolve('late'), 100))
    const p = abortable(pending, controller.signal)
    controller.abort()
    await expect(p).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('已中止的 signal 立即拒绝为 AbortError', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(abortable(Promise.resolve(1), controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    })
  })
})
