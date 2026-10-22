/**
 * 业务看板 + 系统仪表盘 —— 运营指标与质量/记忆/追踪（10 条端点）
 * ------------------------------------------------------------------
 *   业务指标   /api/business/dashboard|definitions|health|metrics/<name>|prometheus
 *   系统仪表盘 /api/dashboard/health|memory|quality|traces|traces/<id>
 *
 * 【为什么并成一页两 Tab】两者都在回答"系统现在怎么样"，但口径不同：
 * 业务指标看的是**产品面**（交互/任务/知识/扩展的计数与价值），
 * 系统仪表盘看的是**运行面**（服务健康、记忆命中率、评审分数分布、追踪）。
 * 分开 Tab 是为了让"我看的是哪一种数"在一眼之内确定。
 *
 * 【prometheus 为什么不走 hubGet】它返回 text/plain 的抓取格式，不是 JSON；
 * 解析失败会被误读成"接口坏了"。故做成下载链接（浏览器直接取）。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Activity, BarChart3, ExternalLink, Gauge, Loader2, RefreshCw, Search, Server,
} from 'lucide-react'
import { ErrorBox, hubGet } from '../components/ui'
import {
  BUSINESS_DASHBOARD,
  BUSINESS_DEFINITIONS,
  BUSINESS_HEALTH,
  BUSINESS_PROMETHEUS,
  DASHBOARD_HEALTH,
  DASHBOARD_MEMORY,
  DASHBOARD_QUALITY,
  DASHBOARD_TRACES,
  businessMetric,
  dashboardTrace,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface MetricDef {
  name?: string
  category?: string
  description?: string
  unit?: string
  aggregation?: string
  metric_type?: string
  business_value?: string
}
interface MetricVal {
  data?: Record<string, unknown> | number
  description?: string
  unit?: string
  business_value?: string
}
interface TraceRow { trace_id?: string; name?: string; duration_ms?: number; status?: string }

type Tab = 'business' | 'dashboard'

export default function BusinessBoard() {
  const [tab, setTab] = useState<Tab>('business')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const [bizDashboard, setBizDashboard] = useState<Record<string, Record<string, MetricVal>> | null>(null)
  const [defs, setDefs] = useState<MetricDef[]>([])
  const [cats, setCats] = useState<Record<string, number>>({})
  const [bizHealth, setBizHealth] = useState<Record<string, unknown> | null>(null)
  const [metricName, setMetricName] = useState('')
  const [metricDetail, setMetricDetail] = useState<Record<string, unknown> | null>(null)
  const [catFilter, setCatFilter] = useState('')

  const [svcHealth, setSvcHealth] = useState<Record<string, unknown> | null>(null)
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null)
  const [mem, setMem] = useState<Record<string, unknown> | null>(null)
  const [traces, setTraces] = useState<TraceRow[]>([])
  const [traceId, setTraceId] = useState('')
  const [traceDetail, setTraceDetail] = useState<Record<string, unknown> | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const loadBusiness = useCallback(async () => {
    setBusy('business'); setErr('')
    try {
      const [dash, dfn, hl] = await Promise.allSettled([
        hubGet<Record<string, Record<string, MetricVal>>>(BUSINESS_DASHBOARD),
        hubGet<{ definitions?: MetricDef[]; categories?: Record<string, number> }>(BUSINESS_DEFINITIONS),
        hubGet<Record<string, unknown>>(BUSINESS_HEALTH),
      ])
      if (dash.status === 'fulfilled') setBizDashboard(dash.value)
      if (dfn.status === 'fulfilled') {
        setDefs(dfn.value?.definitions ?? [])
        setCats(dfn.value?.categories ?? {})
      }
      if (hl.status === 'fulfilled') setBizHealth(hl.value)
    } catch (e) { fail('读取业务指标失败', e) } finally { setBusy('') }
  }, [])

  const loadDashboard = useCallback(async () => {
    setBusy('dashboard'); setErr('')
    try {
      const [h, q, m, t] = await Promise.allSettled([
        hubGet<Record<string, unknown>>(DASHBOARD_HEALTH),
        hubGet<Record<string, unknown>>(DASHBOARD_QUALITY),
        hubGet<Record<string, unknown>>(DASHBOARD_MEMORY),
        hubGet<{ traces?: TraceRow[] }>(DASHBOARD_TRACES + '?limit=20'),
      ])
      if (h.status === 'fulfilled') setSvcHealth(h.value)
      if (q.status === 'fulfilled') setQuality(q.value)
      if (m.status === 'fulfilled') setMem(m.value)
      if (t.status === 'fulfilled') setTraces(t.value?.traces ?? [])
    } catch (e) { fail('读取系统仪表盘失败', e) } finally { setBusy('') }
  }, [])

  const loadMetric = async () => {
    if (!metricName.trim()) return
    setBusy('metric'); setErr(''); setMetricDetail(null)
    try {
      const r = await hubGet<Record<string, unknown>>(businessMetric(metricName.trim()))
      setMetricDetail(r)
    } catch (e) { fail('读取指标详情失败', e) } finally { setBusy('') }
  }

  const loadTrace = async () => {
    if (!traceId.trim()) return
    setBusy('trace'); setErr(''); setTraceDetail(null)
    try {
      const r = await hubGet<Record<string, unknown>>(dashboardTrace(traceId.trim()))
      setTraceDetail(r)
    } catch (e) { fail('读取追踪详情失败', e) } finally { setBusy('') }
  }

  useEffect(() => { void loadBusiness(); void loadDashboard() }, [loadBusiness, loadDashboard])

  const loading = busy !== ''
  const bizCats = Object.keys(bizDashboard ?? {})

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <BarChart3 size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">业务看板</h2>
        <span className="text-[11px] text-slate-500">业务指标（产品面）与系统仪表盘（运行面）</span>
        <div className="ml-auto flex items-center gap-1.5">
          <a href={BUSINESS_PROMETHEUS} target="_blank" rel="noreferrer" className={BTN} aria-label="导出 Prometheus 指标">
            <ExternalLink size={11} /> Prometheus
          </a>
          <button type="button" className={BTN} onClick={() => { void loadBusiness(); void loadDashboard() }} disabled={loading} aria-label="刷新业务看板">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}

      <div className="mb-2 flex items-center gap-1">
        {([['business', '业务指标'], ['dashboard', '系统仪表盘']] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-selected={tab === id}
            className={'rounded-lg px-2.5 py-1 text-[11px] transition-colors ' + (
              tab === id ? 'bg-cyan-500/15 font-medium text-cyan-300' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'business' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {[
              ['业务健康分', bizHealth?.health_score],
              ['状态', bizHealth?.status],
              ['指标定义', defs.length],
              ['指标分类', Object.keys(cats).length],
            ].map(([k, v]) => (
              <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-900/50 px-2 py-1.5">
                <div className="text-[10px] text-slate-500">{String(k)}</div>
                <div className="font-mono text-[13px] text-cyan-300">{String(v ?? '—')}</div>
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              <Gauge size={11} className="text-cyan-400" /> 指标定义（{defs.length}）
              <select className={INPUT} value={catFilter} onChange={(e) => setCatFilter(e.target.value)} aria-label="指标分类筛选">
                <option value="">全部分类</option>
                {Object.keys(cats).map((c) => <option key={c} value={c}>{c}（{cats[c]}）</option>)}
              </select>
              {Object.keys(bizCats).length > 0 && (
                <span className="text-[10px] text-slate-600">在线分组：{bizCats.join(' / ')}</span>
              )}
            </div>
            <div className="max-h-72 overflow-y-auto">
              <table className="w-full text-left text-[11px]">
                <thead className="text-[10px] text-slate-500">
                  <tr>
                    <th className="px-1 py-1">指标</th>
                    <th className="px-1 py-1">分类</th>
                    <th className="px-1 py-1">类型</th>
                    <th className="px-1 py-1">单位</th>
                    <th className="px-1 py-1">说明</th>
                  </tr>
                </thead>
                <tbody>
                  {defs.filter((d) => !catFilter || d.category === catFilter).map((d, i) => (
                    <tr key={(d.name ?? '') + i} className="border-t border-slate-800/60 hover:bg-slate-800/30">
                      <td className="px-1 py-1">
                        <button type="button" className="font-mono text-[10px] text-cyan-300 underline-offset-2 hover:underline" onClick={() => setMetricName(d.name ?? '')}>
                          {d.name ?? ''}
                        </button>
                      </td>
                      <td className="px-1 py-1 text-slate-400">{d.category ?? ''}</td>
                      <td className="px-1 py-1 text-slate-500">{d.metric_type ?? ''}</td>
                      <td className="px-1 py-1 text-slate-500">{d.unit ?? ''}</td>
                      <td className="px-1 py-1 text-slate-500">{d.business_value ?? d.description ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              <Search size={11} className="text-cyan-400" /> 单个指标详情
              <input className={INPUT + ' w-72'} value={metricName} onChange={(e) => setMetricName(e.target.value)} placeholder="指标名，如 yunshu_interaction_total" aria-label="指标名" />
              <button type="button" className={BTN} onClick={() => void loadMetric()} disabled={loading || metricName.trim() === ''} aria-label="查询指标详情">
                {busy === 'metric' ? <Loader2 size={11} className="animate-spin" /> : null} 查询
              </button>
            </div>
            {metricDetail && <pre className={PRE}>{JSON.stringify(metricDetail, null, 2)}</pre>}
          </div>

          {bizHealth && (
            <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
              <div className="mb-1 text-[11px] text-slate-400">业务指标健康检查</div>
              <pre className={PRE}>{JSON.stringify(bizHealth, null, 2)}</pre>
            </div>
          )}
        </div>
      )}

      {tab === 'dashboard' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {[
              ['服务状态', svcHealth?.status],
              ['评审均分', (quality as { critic_scores?: { average_score?: number } })?.critic_scores?.average_score],
              ['评审样本', (quality as { critic_scores?: { total_evaluations?: number } })?.critic_scores?.total_evaluations],
              ['追踪数', traces.length],
            ].map(([k, v]) => (
              <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-900/50 px-2 py-1.5">
                <div className="text-[10px] text-slate-500">{String(k)}</div>
                <div className="truncate font-mono text-[13px] text-cyan-300">{String(v ?? '—')}</div>
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Activity size={11} className="text-cyan-400" /> 服务健康
            </div>
            <pre className={PRE}>{JSON.stringify(svcHealth ?? {}, null, 2)}</pre>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Gauge size={11} className="text-cyan-400" /> 质量监控（评审分数分布与趋势）
            </div>
            <pre className={PRE}>{JSON.stringify(quality ?? {}, null, 2)}</pre>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Server size={11} className="text-cyan-400" /> 记忆使用（命中率与分布）
            </div>
            <pre className={PRE}>{JSON.stringify(mem ?? {}, null, 2)}</pre>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Search size={11} className="text-cyan-400" /> 追踪（{traces.length}）
            </div>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <input className={INPUT + ' w-72'} value={traceId} onChange={(e) => setTraceId(e.target.value)} placeholder="trace_id" aria-label="仪表盘追踪 id" />
              <button type="button" className={BTN} onClick={() => void loadTrace()} disabled={loading || traceId.trim() === ''} aria-label="查询仪表盘追踪详情">
                {busy === 'trace' ? <Loader2 size={11} className="animate-spin" /> : null} 详情
              </button>
            </div>
            {traces.length > 0 && (
              <div className="flex flex-col gap-1">
                {traces.slice(0, 8).map((t, i) => (
                  <button
                    key={(t.trace_id ?? '') + i}
                    type="button"
                    onClick={() => setTraceId(t.trace_id ?? '')}
                    className="flex items-center gap-2 rounded-sm border border-slate-800 bg-slate-900/40 px-2 py-1 text-left text-[11px] hover:bg-slate-800/50"
                  >
                    <span className="font-mono text-[10px] text-cyan-300">{t.trace_id ?? ''}</span>
                    <span className="text-slate-400">{t.name ?? ''}</span>
                    <span className="ml-auto font-mono text-[10px] text-slate-500">{Math.round(t.duration_ms ?? 0)}ms</span>
                  </button>
                ))}
              </div>
            )}
            {traceDetail && <pre className={PRE + ' mt-1'}>{JSON.stringify(traceDetail, null, 2)}</pre>}
          </div>
        </div>
      )}
    </div>
  )
}
