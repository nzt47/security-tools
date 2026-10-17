/**
 * 日志中心面板 —— 独立 /logs 日志应用的只读/半自动接口（10 条）
 * ------------------------------------------------------------------
 *   /logs/api/stats | trends | errors | insights | knowledge | actions | query
 *   /logs/api/introspection/status + run
 *   /logs/dashboard（legacy 页面，仅提供链接）
 *
 * 【为什么单独一个面板，而不是把这些塞进上面那张日志表】
 *   上面那张表是 **运行期结构化日志**（/api/observability/logs，Loki 口径）；
 *   这里读的是 /logs 这个独立日志应用自己的**操作审计库**（operation / category /
 *   status / duration_ms）——两套数据源、两套字段、两套保留策略。
 *   混在一张表里，用户会以为"日志查不到"是过滤写错了。
 *
 * 【/logs/api/* 要令牌】实测无令牌 401（该应用同样走 require_token），
 *   所以这里与 hubGet 一样由 lib/apiToken 注入 Authorization。
 */
import { useCallback, useEffect, useState } from 'react'
import { Activity, AlertTriangle, BookOpen, Lightbulb, Loader2, RefreshCw, Search, Sparkles } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  LOGS_API_ACTIONS,
  LOGS_API_ERRORS,
  LOGS_API_INSIGHTS,
  LOGS_API_INTROSPECTION_RUN,
  LOGS_API_INTROSPECTION_STATUS,
  LOGS_API_KNOWLEDGE,
  LOGS_API_QUERY,
  LOGS_API_STATS,
  LOGS_API_TRENDS,
  LOGS_DASHBOARD,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

type Tab = 'overview' | 'errors' | 'insights' | 'query' | 'introspection'

const TABS: { key: Tab; label: string; icon: typeof Activity }[] = [
  { key: 'overview', label: '统计 / 趋势', icon: Activity },
  { key: 'errors', label: '错误', icon: AlertTriangle },
  { key: 'insights', label: '洞察 / 知识 / 建议', icon: Lightbulb },
  { key: 'query', label: '操作查询', icon: Search },
  { key: 'introspection', label: '内省引擎', icon: Sparkles },
]

interface Stats {
  total_count?: number
  error_rate?: number
  p95_duration_ms?: number
  p99_duration_ms?: number
  avg_duration_ms?: number
  time_range_hours?: number
  by_level?: Record<string, number>
  by_category?: Record<string, number>
  top_sources?: { source?: string; count?: number }[]
}

interface Trends {
  hours?: number
  performance_trend?: { hour?: string; avg_ms?: number; count?: number }[]
  error_trend?: { hour?: string; count?: number }[]
}

interface OpRow {
  id?: number
  created_at?: string
  level?: string
  category?: string
  operation?: string
  source?: string
  status?: string
  duration_ms?: number
  message?: string
}

interface InsightRow {
  id?: number
  type?: string
  summary?: string
  confidence?: number
  created_at?: string
  source?: string
}

interface KnowledgeRow {
  id?: number
  domain?: string
  finding?: string
  confidence?: number
  created_at_iso?: string
}

interface ActionRow {
  id?: number
  title?: string
  summary?: string
  priority?: string
  status?: string
}

interface IntrospectionStatus {
  running?: boolean
  thread_alive?: boolean
  total_cycles?: number
  last_run?: string
  is_idle?: boolean
  seconds_until_idle?: number
}

export default function LogCenterPanel() {
  const [tab, setTab] = useState<Tab>('overview')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const [stats, setStats] = useState<Stats | null>(null)
  const [trends, setTrends] = useState<Trends | null>(null)
  const [errors, setErrors] = useState<OpRow[]>([])
  const [insights, setInsights] = useState<InsightRow[]>([])
  const [knowledge, setKnowledge] = useState<KnowledgeRow[]>([])
  const [actions, setActions] = useState<ActionRow[]>([])
  const [actionsMeta, setActionsMeta] = useState<{ open?: number; high?: number }>({})
  const [rows, setRows] = useState<OpRow[]>([])
  const [insight, setInsight] = useState<IntrospectionStatus | null>(null)

  const [q, setQ] = useState('')
  const [level, setLevel] = useState('')
  const [limit, setLimit] = useState('20')

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const loadOverview = useCallback(async () => {
    setBusy('overview'); setErr('')
    try {
      const [s, t] = await Promise.all([
        hubGet<Stats>(LOGS_API_STATS),
        hubGet<Trends>(LOGS_API_TRENDS),
      ])
      setStats((s as { ok?: boolean })?.ok === false ? null : s)
      setTrends((t as { ok?: boolean })?.ok === false ? null : t)
    } catch (e) { fail('读取日志统计失败', e) } finally { setBusy('') }
  }, [])

  const loadErrors = useCallback(async () => {
    setBusy('errors'); setErr('')
    try {
      const r = await hubGet<{ results?: OpRow[]; total?: number }>(LOGS_API_ERRORS)
      setErrors(r?.results ?? [])
    } catch (e) { fail('读取错误列表失败', e) } finally { setBusy('') }
  }, [])

  const loadInsights = useCallback(async () => {
    setBusy('insights'); setErr('')
    try {
      const [i, k, a] = await Promise.all([
        hubGet<{ results?: InsightRow[] }>(LOGS_API_INSIGHTS),
        hubGet<{ results?: KnowledgeRow[] }>(LOGS_API_KNOWLEDGE),
        hubGet<{ results?: ActionRow[]; open_count?: number; high_priority_count?: number }>(LOGS_API_ACTIONS),
      ])
      setInsights(i?.results ?? [])
      setKnowledge(k?.results ?? [])
      setActions(a?.results ?? [])
      setActionsMeta({ open: a?.open_count ?? 0, high: a?.high_priority_count ?? 0 })
    } catch (e) { fail('读取洞察失败', e) } finally { setBusy('') }
  }, [])

  const loadIntrospection = useCallback(async () => {
    setBusy('introspection'); setErr('')
    try {
      const r = await hubGet<IntrospectionStatus>(LOGS_API_INTROSPECTION_STATUS)
      setInsight((r as { ok?: boolean })?.ok === false ? null : r)
    } catch (e) { fail('读取内省状态失败', e) } finally { setBusy('') }
  }, [])

  const runQuery = useCallback(async () => {
    setBusy('query'); setErr('')
    try {
      const params = '?limit=' + encodeURIComponent(limit || '20')
        + (level ? '&level=' + encodeURIComponent(level) : '')
        + (q.trim() ? '&q=' + encodeURIComponent(q.trim()) : '')
      const r = await hubGet<{ results?: OpRow[]; total?: number }>(LOGS_API_QUERY + params)
      setRows(r?.results ?? [])
    } catch (e) { fail('查询操作日志失败', e) } finally { setBusy('') }
  }, [limit, level, q])

  const triggerIntrospection = async () => {
    setBusy('run-introspection'); setErr(''); setNote('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(LOGS_API_INTROSPECTION_RUN, {})
      if (r?.ok === false) {
        setErr('触发内省失败：' + (r?.error || r?.message || '后端返回 ok=false'))
        return
      }
      setNote('已触发内省分析' + (r?.message ? '：' + r.message : ''))
      await loadIntrospection()
    } catch (e) { fail('触发内省失败', e) } finally { setBusy('') }
  }

  useEffect(() => {
    if (tab === 'overview' && !stats) void loadOverview()
    if (tab === 'errors' && errors.length === 0) void loadErrors()
    if (tab === 'insights' && insights.length === 0 && knowledge.length === 0) void loadInsights()
    if (tab === 'query' && rows.length === 0) void runQuery()
    if (tab === 'introspection' && !insight) void loadIntrospection()
  }, [tab, stats, errors.length, insights.length, knowledge.length, rows.length, insight, loadOverview, loadErrors, loadInsights, runQuery, loadIntrospection])

  const loading = busy !== ''

  return (
    <div className="mt-5 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold text-slate-200">日志中心（独立日志应用）</h3>
        <span className="text-[10px] text-slate-500">
          操作审计库：类别 / 状态 / 耗时 / 洞察 / 行动建议 —— 与上面的运行期日志是两套数据源
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {/* 这个链接是 /logs/dashboard 的唯一入口：该页面是独立应用的 legacy HTML 页 */}
          <a href={LOGS_DASHBOARD} target="_blank" rel="noreferrer" className={BTN} aria-label="打开日志仪表盘">
            打开日志仪表盘 ↗
          </a>
          <button
            type="button"
            className={BTN}
            aria-label="刷新日志中心"
            disabled={loading}
            onClick={() => {
              if (tab === 'overview') void loadOverview()
              else if (tab === 'errors') void loadErrors()
              else if (tab === 'insights') void loadInsights()
              else if (tab === 'query') void runQuery()
              else void loadIntrospection()
            }}
          >
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-1">
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              aria-selected={tab === t.key}
              className={'flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] transition-colors ' + (
                tab === t.key
                  ? 'bg-cyan-500/15 font-medium text-cyan-300'
                  : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              )}
            >
              <Icon size={11} />{t.label}
            </button>
          )
        })}
      </div>

      {err && <ErrorBox message={err} />}
      {note && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{note}</div>}

      {tab === 'overview' && (
        <div className="space-y-2">
          {stats ? (
            <>
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                {[
                  ['总条数', stats.total_count ?? 0],
                  ['错误率', ((stats.error_rate ?? 0) * 100).toFixed(2) + '%'],
                  ['P95 耗时', (stats.p95_duration_ms ?? 0) + 'ms'],
                  ['P99 耗时', (stats.p99_duration_ms ?? 0) + 'ms'],
                ].map(([k, v]) => (
                  <div key={String(k)} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5">
                    <div className="text-[10px] text-slate-500">{String(k)}</div>
                    <div className="font-mono text-sm text-cyan-300">{String(v)}</div>
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-3 text-[11px] text-slate-400">
                <span>级别：{Object.entries(stats.by_level ?? {}).map(([k, v]) => k + '=' + v).join(' · ') || '—'}</span>
                <span>类别：{Object.entries(stats.by_category ?? {}).map(([k, v]) => k + '=' + v).join(' · ') || '—'}</span>
              </div>
              {(stats.top_sources?.length ?? 0) > 0 && (
                <div className="text-[11px] text-slate-400">
                  来源 Top：{stats.top_sources?.map((s) => (s.source ?? '?') + '(' + (s.count ?? 0) + ')').join(' · ')}
                </div>
              )}
            </>
          ) : <div className="text-[11px] text-slate-500">未取到统计。</div>}

          {trends && (
            <div className="text-[11px] text-slate-400">
              <div>窗口 {trends.hours ?? 24} 小时 · 性能点 {(trends.performance_trend ?? []).length} 个 · 错误点 {(trends.error_trend ?? []).length} 个</div>
              {(trends.performance_trend?.length ?? 0) > 0 && (
                <div className="mt-1 flex flex-wrap gap-1 font-mono text-[10px] text-slate-500">
                  {trends.performance_trend?.slice(0, 24).map((p, i) => (
                    <span key={i} className="rounded bg-slate-800/60 px-1" title={p.hour}>
                      {p.hour?.slice(5, 13) ?? ''}={Math.round(p.avg_ms ?? 0)}ms
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {tab === 'errors' && (
        <div className="flex flex-col gap-1">
          {errors.length === 0 && <div className="text-[11px] text-slate-500">窗口内没有错误记录。</div>}
          {errors.map((r, i) => (
            <div key={(r.id ?? '') + '-' + i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[10px] text-slate-500">{r.created_at ?? ''}</span>
                <span className="text-red-400">{r.level ?? 'error'}</span>
                <span className="font-mono text-[10px] text-cyan-300">{r.operation ?? r.source ?? ''}</span>
                <span className="truncate text-slate-300">{r.message ?? ''}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'insights' && (
        <div className="space-y-2">
          <div>
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Lightbulb size={11} className="text-amber-400" /> 洞察（{insights.length}）
            </div>
            <div className="flex flex-col gap-1">
              {insights.slice(0, 12).map((r, i) => (
                <div key={(r.id ?? '') + '-' + i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="mr-2 rounded bg-slate-800 px-1.5 text-[9px] text-slate-400">{r.type ?? r.source ?? '?'}</span>
                  <span className="text-slate-300">{r.summary ?? ''}</span>
                  <span className="ml-2 font-mono text-[10px] text-slate-600">conf={r.confidence ?? '-'}</span>
                </div>
              ))}
              {insights.length === 0 && <div className="text-[10px] text-slate-600">—</div>}
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <BookOpen size={11} className="text-cyan-400" /> 知识发现（{knowledge.length}）
            </div>
            <div className="flex flex-col gap-1">
              {knowledge.slice(0, 12).map((r, i) => (
                <div key={(r.id ?? '') + '-k' + i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="mr-2 rounded bg-slate-800 px-1.5 text-[9px] text-slate-400">{r.domain ?? '?'}</span>
                  <span className="text-slate-300">{r.finding ?? ''}</span>
                  <span className="ml-2 font-mono text-[10px] text-slate-600">{r.created_at_iso ?? ''}</span>
                </div>
              ))}
              {knowledge.length === 0 && <div className="text-[10px] text-slate-600">—</div>}
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <AlertTriangle size={11} className="text-amber-400" /> 行动建议（未处理 {actionsMeta.open ?? 0} · 高优先 {actionsMeta.high ?? 0}）
            </div>
            <div className="flex flex-col gap-1">
              {actions.slice(0, 12).map((r, i) => (
                <div key={(r.id ?? '') + '-a' + i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="text-slate-300">{r.title ?? r.summary ?? '(无标题)'}</span>
                  <span className="ml-2 rounded bg-slate-800 px-1.5 text-[9px] text-slate-400">{r.priority ?? r.status ?? ''}</span>
                </div>
              ))}
              {actions.length === 0 && <div className="text-[10px] text-slate-600">—</div>}
            </div>
          </div>
        </div>
      )}

      {tab === 'query' && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <input className={INPUT + ' w-52'} placeholder="关键字" value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void runQuery() }} aria-label="操作日志关键字" />
            <select className={INPUT} value={level} onChange={(e) => setLevel(e.target.value)} aria-label="操作日志级别">
              <option value="">全部级别</option>
              <option value="debug">debug</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
            <select className={INPUT} value={limit} onChange={(e) => setLimit(e.target.value)} aria-label="操作日志条数">
              <option value="20">20 条</option>
              <option value="50">50 条</option>
              <option value="100">100 条</option>
            </select>
            <button type="button" className={BTN_EM} onClick={() => void runQuery()} disabled={loading} aria-label="查询操作日志">
              {busy === 'query' ? <Loader2 size={11} className="animate-spin" /> : <Search size={11} />} 查询
            </button>
          </div>
          <div className="max-h-72 overflow-y-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="text-[10px] text-slate-500">
                <tr>
                  <th className="px-1 py-1">时间</th>
                  <th className="px-1 py-1">级别</th>
                  <th className="px-1 py-1">类别</th>
                  <th className="px-1 py-1">操作</th>
                  <th className="px-1 py-1">来源</th>
                  <th className="px-1 py-1">耗时</th>
                  <th className="px-1 py-1">消息</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={(r.id ?? '') + '-q' + i} className="border-t border-slate-800/60">
                    <td className="px-1 py-1 font-mono text-[10px] text-slate-500">{r.created_at ?? ''}</td>
                    <td className="px-1 py-1 text-slate-400">{r.level ?? ''}</td>
                    <td className="px-1 py-1 text-slate-400">{r.category ?? ''}</td>
                    <td className="px-1 py-1 font-mono text-[10px] text-cyan-300">{r.operation ?? ''}</td>
                    <td className="px-1 py-1 text-slate-500">{r.source ?? ''}</td>
                    <td className="px-1 py-1 font-mono text-[10px] text-slate-500">{Math.round(r.duration_ms ?? 0)}ms</td>
                    <td className="px-1 py-1 text-slate-300">{r.message ?? ''}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={7} className="px-1 py-3 text-center text-[10px] text-slate-600">没有匹配的记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'introspection' && (
        <div className="space-y-2">
          {insight ? (
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {[
                ['线程存活', insight.thread_alive ? '是' : '否'],
                ['正在运行', insight.running ? '是' : '否'],
                ['累计轮次', insight.total_cycles ?? 0],
                ['上次运行', insight.last_run ?? 'never'],
              ].map(([k, v]) => (
                <div key={String(k)} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5">
                  <div className="text-[10px] text-slate-500">{String(k)}</div>
                  <div className="font-mono text-[12px] text-cyan-300">{String(v)}</div>
                </div>
              ))}
            </div>
          ) : <div className="text-[11px] text-slate-500">未取到内省状态。</div>}
          <button type="button" className={BTN_EM} onClick={() => void triggerIntrospection()} disabled={loading || insight?.running === true} aria-label="手动触发内省">
            {busy === 'run-introspection' ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />} 手动触发一次内省
          </button>
        </div>
      )}
    </div>
  )
}
