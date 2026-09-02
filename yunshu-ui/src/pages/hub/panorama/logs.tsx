/**
 * 日志查看 —— 结构化日志浏览
 * 数据源：/api/observability/logs（可带 level / limit / query 过滤）
 */
import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Card, Loading, ErrorBox, PageHeader, hubGet, unwrap } from '../components/ui'

interface LogEntry {
  timestamp?: string
  time?: string
  level?: string
  logger?: string
  module?: string
  message?: string
  msg?: string
  [k: string]: unknown
}

const LEVEL_COLOR: Record<string, string> = {
  DEBUG: 'text-slate-500',
  INFO: 'text-cyan-400',
  WARNING: 'text-amber-400',
  WARN: 'text-amber-400',
  ERROR: 'text-red-400',
  CRITICAL: 'text-red-500',
}

export default function PanoramaLogs() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [level, setLevel] = useState('')
  const [limit, setLimit] = useState(100)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    const params = new URLSearchParams()
    if (level) params.set('level', level)
    params.set('limit', String(limit))
    if (query) params.set('query', query)
    hubGet(`/api/observability/logs?${params.toString()}`)
      .then((r) => {
        const d = unwrap<LogEntry[] | { logs?: LogEntry[] }>(r as Record<string, unknown>)
        const arr = Array.isArray(d) ? d : (d as { logs?: LogEntry[] }).logs ?? []
        setLogs(arr)
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
        description="结构化运行日志（来源：/api/observability/logs）"
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
            {logs.length === 0 && <div className="py-10 text-center text-slate-500">暂无日志</div>}
            {logs.map((l, i) => (
              <div key={i} className="flex gap-3 rounded px-2 py-1 hover:bg-slate-800/40">
                <span className="shrink-0 text-slate-600">{String(l.timestamp ?? l.time ?? '')}</span>
                <span className={`w-16 shrink-0 font-semibold ${LEVEL_COLOR[String(l.level ?? '').toUpperCase()] ?? 'text-slate-400'}`}>
                  {String(l.level ?? '').toUpperCase()}
                </span>
                <span className="w-40 shrink-0 truncate text-slate-500">{String(l.logger ?? l.module ?? '')}</span>
                <span className="min-w-0 flex-1 whitespace-pre-wrap text-slate-300">{String(l.message ?? l.msg ?? JSON.stringify(l))}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
