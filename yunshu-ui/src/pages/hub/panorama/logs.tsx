/**
 * 日志查看 —— 结构化日志浏览
 * 数据源：/api/observability/logs（可带 level / limit / query 过滤）
 *
 * 【2026-10-15 修复 · 实测三处读错】
 *   ① 后端每条日志把真实字段放在 labels 里（labels.level / labels.logger / labels.app）；
 *      顶层只有 timestamp / labels / message。原实现读顶层 l.level/l.logger ⇒ 永远空。
 *   ② timestamp 是 Unix 秒（float），不是字符串；原实现直接 String() 会显示 1791…。
 *   ③ 空结果原样渲染空表，看不出"到底查了哪儿"；现在把后端本地回退实际扫描的窗口/文件
 *      如实上屏（诊断提示）。
 * 过滤与时间窗判定**全部**在后端 agent/monitoring/loki.py，本页只展示。
 */
import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Card, Loading, ErrorBox, PageHeader, hubGet, unwrap } from '../components/ui'

interface LogEntry {
  timestamp?: string | number
  time?: string
  level?: string
  logger?: string
  module?: string
  message?: string
  msg?: string
  labels?: Record<string, unknown>
  [k: string]: unknown
}

/** 后端本地回退时回填的"实际扫描窗口"（见 agent/monitoring/loki.py） */
interface ScanWindow {
  source?: string
  window_start?: string
  window_end?: string
  window_days?: number | null
  files_scanned?: string[]
  files_in_window?: string[]
  used_latest_files_fallback?: boolean
  matched?: number
}

interface LogsResponse {
  logs?: LogEntry[]
  total?: number
  window?: ScanWindow
}

const LEVEL_COLOR: Record<string, string> = {
  DEBUG: 'text-slate-500',
  INFO: 'text-cyan-400',
  WARNING: 'text-amber-400',
  WARN: 'text-amber-400',
  ERROR: 'text-red-400',
  CRITICAL: 'text-red-500',
}

/** 顶层优先、labels 兜底地取字段（labels 才是后端的真实字段位置） */
function fieldOf(l: LogEntry, ...keys: string[]): string {
  for (const k of keys) {
    const top = l[k]
    if (top !== undefined && top !== null && top !== '') return String(top)
    const label = l.labels?.[k.replace(/^labels\./, '')]
    if (label !== undefined && label !== null && label !== '') return String(label)
  }
  return ''
}

/** Unix 秒（或毫秒）→ 本地时间；字符串原样返回 */
function formatTimestamp(ts: unknown): string {
  if (typeof ts === 'number' && Number.isFinite(ts)) {
    const ms = ts > 1e12 ? ts : ts * 1000
    const d = new Date(ms)
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  return ts === undefined || ts === null || ts === '' ? '' : String(ts)
}

export default function PanoramaLogs() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [level, setLevel] = useState('')
  const [limit, setLimit] = useState(100)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [scan, setScan] = useState<ScanWindow | null>(null)

  const load = () => {
    setLoading(true)
    setError('')
    const params = new URLSearchParams()
    if (level) params.set('level', level)
    params.set('limit', String(limit))
    if (query) params.set('query', query)
    hubGet(`/api/observability/logs?${params.toString()}`)
      .then((r) => {
        const d = unwrap<LogsResponse | LogEntry[]>(r as Record<string, unknown>)
        const arr = Array.isArray(d) ? d : d.logs ?? []
        setLogs(arr)
        setScan(Array.isArray(d) ? null : d.window ?? null)
        setLoading(false)
      })
      .catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load 每次渲染重建；初载 + 10s 轮询语义固定
  }, [])

  return (
    <div className="p-6">
      <PageHeader
        title="日志查看"
        description="结构化运行日志（来源：/api/observability/logs；过滤与时间窗由后端 agent/monitoring/loki.py 判定）"
        actions={
          <div className="flex items-center gap-2">
            <select value={level} onChange={(e) => setLevel(e.target.value)} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-300">
              <option value="">全部级别</option>
              <option value="DEBUG">DEBUG</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
            </select>
            <input
              value={query} onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && load()}
              placeholder="关键字过滤"
              className="w-40 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600 outline-none"
            />
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-300">
              <option value={50}>50 条</option>
              <option value={100}>100 条</option>
              <option value={200}>200 条</option>
            </select>
            <button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RefreshCw size={12} /> 查询
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <Card>
          <div className="max-h-[70vh] space-y-0.5 overflow-y-auto font-mono text-xs">
            {logs.length === 0 && (
              <div className="py-10 text-center" data-testid="logs-empty">
                <div className="text-sm text-slate-400">没有匹配的日志</div>
                <div className="mt-2 text-xs text-slate-600">
                  筛选：级别 {level || '全部'} · 关键字 {query || '无'}
                  {scan?.source === 'local' && (
                    <>
                      <br />
                      已扫描的窗口：{scan.window_start ?? '?'} ~ {scan.window_end ?? '?'}
                      {scan.window_days ? `（最近 ${scan.window_days} 天）` : ''}
                      {scan.files_scanned && scan.files_scanned.length > 0
                        ? `；文件：${scan.files_scanned.join('、')}`
                        : '；窗口内没有本地日志文件'}
                    </>
                  )}
                </div>
              </div>
            )}
            {logs.map((l, i) => {
              const lvl = fieldOf(l, 'level').toUpperCase()
              const ts = formatTimestamp(l.timestamp ?? l.time)
              const logger = fieldOf(l, 'logger', 'module', 'app')
              const msg = fieldOf(l, 'message', 'msg') || JSON.stringify(l)
              return (
                <div key={i} data-testid={`log-row-${i}`} className="flex gap-3 rounded px-2 py-1 hover:bg-slate-800/40">
                  <span className="shrink-0 text-slate-600">{ts}</span>
                  <span className={`w-16 shrink-0 font-semibold ${LEVEL_COLOR[lvl] ?? 'text-slate-400'}`}>
                    {lvl}
                  </span>
                  <span className="w-40 shrink-0 truncate text-slate-500" title={logger}>{logger}</span>
                  <span className="min-w-0 flex-1 whitespace-pre-wrap text-slate-300">{msg}</span>
                </div>
              )
            })}
          </div>
        </Card>
      )}
    </div>
  )
}
