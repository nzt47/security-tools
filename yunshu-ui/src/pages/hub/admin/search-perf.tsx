/**
 * 搜索性能监控 —— 启停 / 状态 / 摘要 / 历史 / 手动检测（6 条）
 * ------------------------------------------------------------------
 *   GET  /api/search-performance/status    运行状态（是否在跑、间隔、上次检测）
 *   GET  /api/search-performance/summary   最近一次的健康摘要（成功率/失败数）
 *   GET  /api/search-performance/history   历史检测记录
 *   POST /api/search-performance/start     启动后台轮询
 *   POST /api/search-performance/stop      停止
 *   POST /api/search-performance/check     立刻检测一次
 *
 * 【为什么"离开页面要不要停"必须由人决定】这个监控跑起来会持续打各搜索引擎。
 * 页面关闭/切走不会自动停 —— 若前端偷偷在卸载时 stop，用户以为还在监控，
 * 实际早停了；反过来若自动 start，就是替用户做了付费/限流决定。
 * 所以这里只提供显式按钮，并在页面上写清当前是否在跑。
 */
import { useCallback, useEffect, useState } from 'react'
import { Activity, Loader2, Play, RefreshCw, Square, Zap } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  SEARCH_PERF_CHECK,
  SEARCH_PERF_HISTORY,
  SEARCH_PERF_START,
  SEARCH_PERF_STATUS,
  SEARCH_PERF_STOP,
  SEARCH_PERF_SUMMARY,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface PerfStatus {
  running?: boolean
  interval?: number
  check_count?: number
  history_count?: number
  last_check?: string | null
}

interface PerfSummary {
  last_status?: string
  total_checks?: number
  tavily_success_rate?: number
  tavily_success_count?: number
  tavily_failed_count?: number
  tavily_avg_time?: number
}

export default function SearchPerformance() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [status, setStatus] = useState<PerfStatus | null>(null)
  const [summary, setSummary] = useState<PerfSummary | null>(null)
  const [history, setHistory] = useState<Record<string, unknown>[]>([])

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [st, su, hi] = await Promise.allSettled([
        hubGet<{ status?: PerfStatus }>(SEARCH_PERF_STATUS),
        hubGet<{ summary?: PerfSummary }>(SEARCH_PERF_SUMMARY),
        hubGet<{ history?: Record<string, unknown>[] }>(SEARCH_PERF_HISTORY + '?limit=20'),
      ])
      if (st.status === 'fulfilled') setStatus(st.value?.status ?? null)
      if (su.status === 'fulfilled') setSummary(su.value?.summary ?? null)
      if (hi.status === 'fulfilled') setHistory(hi.value?.history ?? [])
    } catch (e) { fail('读取搜索性能失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (which: 'start' | 'stop' | 'check') => {
    setBusy(which); setErr(''); setMsg('')
    const url = which === 'start' ? SEARCH_PERF_START : which === 'stop' ? SEARCH_PERF_STOP : SEARCH_PERF_CHECK
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(url, {})
      if (r?.ok === false) { setErr('操作失败：' + (r.error || 'ok=false')); return }
      setMsg(which === 'start' ? '已启动后台性能监控' : which === 'stop' ? '已停止' : '已立刻检测一次')
      await load()
    } catch (e) { fail('操作失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const running = status?.running === true

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Activity size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">搜索性能监控</h2>
        <span className={'text-[11px] ' + (running ? 'text-emerald-400' : 'text-slate-500')}>
          {running ? '正在运行（' + (status?.interval ?? '?') + ' 秒间隔）' : '未运行'}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label="刷新搜索性能">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
          <button type="button" className={BTN_EM} onClick={() => void act('check')} disabled={loading} aria-label="立刻检测一次">
            {busy === 'check' ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />} 立刻检测
          </button>
          {running ? (
            <button type="button" className={BTN_RED} onClick={() => void act('stop')} disabled={loading} aria-label="停止性能监控">
              {busy === 'stop' ? <Loader2 size={11} className="animate-spin" /> : <Square size={11} />} 停止
            </button>
          ) : (
            <button type="button" className={BTN_EM} onClick={() => void act('start')} disabled={loading} aria-label="启动性能监控">
              {busy === 'start' ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} 启动
            </button>
          )}
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['累计检测', status?.check_count ?? 0],
          ['历史条数', status?.history_count ?? 0],
          ['最近状态', summary?.last_status ?? '—'],
          ['成功率', ((summary?.tavily_success_rate ?? 0) * 100).toFixed(1) + '%'],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="truncate font-mono text-[12px] text-cyan-300">{String(v)}</div>
          </div>
        ))}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-1 text-[11px] text-slate-400">
          最近摘要（成功 {summary?.tavily_success_count ?? 0} / 失败 {summary?.tavily_failed_count ?? 0} · 平均 {Math.round(summary?.tavily_avg_time ?? 0)}ms）
        </div>
        <pre className={PRE}>{JSON.stringify(summary ?? {}, null, 2)}</pre>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-1 text-[11px] text-slate-400">检测历史（{history.length}）</div>
        <div className="flex flex-col gap-1">
          {history.slice(0, 10).map((h, i) => (
            <div key={i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[10px] text-slate-500">{String(h.timestamp ?? '')}</span>
                <span className={(h.status === 'ok' ? 'text-emerald-400' : 'text-amber-400')}>{String(h.status ?? '')}</span>
                <span className="ml-auto font-mono text-[10px] text-slate-600">#{String(h.check_id ?? '')}</span>
              </div>
              {(h.errors as string[] | undefined)?.length ? (
                <div className="text-[10px] text-red-300">{(h.errors as string[]).slice(0, 2).join('；')}</div>
              ) : null}
            </div>
          ))}
          {history.length === 0 && <div className="text-[11px] text-slate-500">还没有检测记录。</div>}
        </div>
      </div>
    </div>
  )
}
