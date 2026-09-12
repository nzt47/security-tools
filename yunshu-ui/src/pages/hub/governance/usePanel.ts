/**
 * 面板通用 hook（§0.3 口径纪律的前端落点）
 * ------------------------------------------------------------------
 * - `usePanel`：统一的 GET + loading/error/reload（AbortController 取消）
 * - `useSecurityState`：★ U1 —— 安全渲染 / 边界词常量的**进程内单例**读取。
 *   前端**不得**硬编码五类边界词、60s 上限、TaintBadge class 名或审批区 z-index；
 *   常量只有一个来源：`GET /api/cp/security/render-state`。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSecurityRenderState } from '@/lib/cpPanelsApi'
import type { SecurityRenderState } from '@/lib/cpPanelsTypes'

export interface PanelState<T> {
  data: T | null
  loading: boolean
  error: string
  reload: () => void
}

/** 通用面板数据 hook */
export function usePanel<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
): PanelState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tick, setTick] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    const controller = new AbortController()
    let alive = true
    setLoading(true)
    setError('')
    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (alive) {
          setData(result)
          setLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (!alive) return
        const err = e as { name?: string; message?: string; code?: string }
        if (err?.name === 'AbortError' || err?.code === 'API_REQUEST_ABORTED') return
        setError(err?.message || String(e))
        setLoading(false)
      })
    return () => {
      alive = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((n) => n + 1), [])
  return { data, loading, error, reload }
}

// ── U1 常量单例 ────────────────────────────────────────────

let _state: SecurityRenderState | null = null
let _inflight: Promise<SecurityRenderState> | null = null
const _subscribers = new Set<(s: SecurityRenderState) => void>()

/** 读取（必要时拉取一次）安全渲染 / 边界词常量；进程内缓存 */
export async function loadSecurityState(): Promise<SecurityRenderState> {
  if (_state) return _state
  if (!_inflight) {
    _inflight = fetchSecurityRenderState()
      .then((s) => {
        _state = s
        _subscribers.forEach((fn) => fn(s))
        return s
      })
      .finally(() => {
        _inflight = null
      })
  }
  return _inflight
}

/** 测试隔离用：清空常量缓存 */
export function resetSecurityStateCache(): void {
  _state = null
  _inflight = null
}

/**
 * 安全渲染 / 边界词常量 hook
 *
 * ★ 返回值可能为 `null`（首次加载中）。**任何依赖这些常量的 UI 在 null 时必须
 *   拒绝渲染相关元素**（例如：拿不到 TaintBadge class 名就不渲染徽章，
 *   而不是"先用一个默认 class 顶上"——那正是 U1 禁止的自定义）。
 */
export function useSecurityState(): {
  state: SecurityRenderState | null
  loading: boolean
  error: string
} {
  const [state, setState] = useState<SecurityRenderState | null>(_state)
  const [loading, setLoading] = useState(!_state)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    if (_state) {
      setState(_state)
      setLoading(false)
      return
    }
    const notify = (s: SecurityRenderState) => {
      if (alive) setState(s)
    }
    _subscribers.add(notify)
    loadSecurityState()
      .then(() => {
        if (alive) setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      })
    return () => {
      alive = false
      _subscribers.delete(notify)
    }
  }, [])

  return { state, loading, error }
}

// ── 数值格式化（**缺位一律渲染 "—"，绝不渲染 0**）──────────

/** 指标数值格式化（`null` ⇒ "—"；带单位与千分位） */
export function formatNumber(
  value: number | null | undefined,
  opts: { unit?: string; digits?: number } = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const digits = opts.digits ?? (Number.isInteger(value) ? 0 : 2)
  const text = value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return opts.unit ? `${text} ${opts.unit}` : text
}

/** 比率 → 百分比文本（仅在**后端已给出可追溯比率**时使用） */
export function formatPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** 毫秒 → 人读 */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
  return `${value.toFixed(value < 10 ? 3 : 1)} ms`
}

/** 分（cents） → ¥ */
export function formatCents(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `¥${(value / 100).toFixed(2)}`
}

/** 时间戳（ISO 或 epoch 秒）→ 本地可读（**相对时间优先，避免时区用例脆弱**） */
export function formatTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  let ms: number
  if (typeof value === 'number') {
    ms = value < 1e12 ? value * 1000 : value
  } else {
    const parsed = Date.parse(value)
    if (Number.isNaN(parsed)) return value
    ms = parsed
  }
  const diff = Date.now() - ms
  const abs = Math.abs(diff)
  if (abs < 60_000) return `${Math.round(abs / 1000)} 秒前`
  if (abs < 3_600_000) return `${Math.round(abs / 60_000)} 分钟前`
  if (abs < 86_400_000) return `${Math.round(abs / 3_600_000)} 小时前`
  return new Date(ms).toLocaleString('zh-CN')
}
