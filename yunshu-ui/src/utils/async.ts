/**
 * 异步工具：防抖 / 节流 / AbortController 辅助
 * ------------------------------------------------------
 * - debounce：延迟执行，间隔内再次调用重置计时（搜索框输入等高频场景）
 * - throttle：固定间隔内最多执行一次（滚动 / 广播等节流场景）
 * - isAbortError：判定主动取消（DOMException AbortError），供调用方区分"取消"与"失败"
 * - abortable：signal 触发时拒绝为 AbortError，统一中止语义（对齐 sse.ts / useLayoutStore）
 */

/** 带 cancel 的防抖函数 */
export interface Debounced<Args extends unknown[]> {
  (...args: Args): void
  /** 取消未执行的调用 */
  cancel(): void
}

/** 带 cancel 的节流函数 */
export interface Throttled<Args extends unknown[]> {
  (...args: Args): void
  /** 取消挂起中的 trailing 调用 */
  cancel(): void
}

/** 防抖：delay 毫秒内无新调用才执行；返回的函数带 cancel() */
export function debounce<Args extends unknown[]>(
  fn: (...args: Args) => void,
  delay: number,
): Debounced<Args> {
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastArgs: Args | null = null

  const debounced = (...args: Args) => {
    lastArgs = args
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      if (lastArgs !== null) fn(...lastArgs)
      lastArgs = null
    }, delay)
  }

  debounced.cancel = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
    lastArgs = null
  }

  return debounced
}

/**
 * 节流：interval 毫秒内最多执行一次（leading 模式：立即执行一次，间隔内调用被丢弃）。
 * 采用"首调用立即执行 + 间隔后允许下一次"的简化实现，适用于列表刷新等场景；
 * 如需 trailing 补执行请改用完整版（当前无此需求，见总览 4.2 反模式"为假设需求抽象"）。
 */
export function throttle<Args extends unknown[]>(
  fn: (...args: Args) => void,
  interval: number,
): Throttled<Args> {
  let lastRun = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastArgs: Args | null = null

  const run = (args: Args) => {
    lastRun = Date.now()
    fn(...args)
  }

  const throttled = (...args: Args) => {
    const now = Date.now()
    if (now - lastRun >= interval) {
      if (timer !== null) clearTimeout(timer)
      timer = null
      lastArgs = null
      run(args)
    } else {
      // 间隔内：仅记录最新参数，供 trailing 兜底（若尚未有 trailing 定时器）
      lastArgs = args
      if (timer === null) {
        timer = setTimeout(() => {
          timer = null
          if (lastArgs !== null) {
            const pending = lastArgs
            lastArgs = null
            run(pending)
          }
        }, interval - (now - lastRun))
      }
    }
  }

  throttled.cancel = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
    lastArgs = null
  }

  return throttled
}

/** 判定是否为主动取消（AbortError），供 catch 分支区分"停止生成/取消请求"与"真实失败" */
export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * 将 Promise 接入 AbortSignal：signal 中止时拒绝为 AbortError（而非让在途 Promise 悬挂）。
 * 用法：`const data = await abortable(apiCall(), signal)`
 */
export function abortable<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise
  if (signal.aborted) {
    return Promise.reject(new DOMException('The operation was aborted.', 'AbortError'))
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      reject(new DOMException('The operation was aborted.', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
    promise.then(
      (value) => {
        signal.removeEventListener('abort', onAbort)
        resolve(value)
      },
      (err) => {
        signal.removeEventListener('abort', onAbort)
        reject(err)
      },
    )
  })
}
