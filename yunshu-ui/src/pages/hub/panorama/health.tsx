/**
 * 健康仪表盘 —— 从根路径 health_dashboard.html 移植（React 版）
 * ------------------------------------------------
 * 功能（与原 / 页面一致）：
 *   - 综合健康度趋势图（overall 随时间变化）
 *   - 五层探针（L1~L5）变化曲线
 *   - 探针明细表（available / score / detail）
 *   - 运行告警（observability/alerts）
 * 数据源：/api/health/probe-trend、/api/observability/alerts、/api/diagnostics/metrics
 * 图表库：recharts（深色科技风，玻璃拟态边框）
 */
import { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area,
} from 'recharts'
import { Activity, AlertTriangle, RefreshCw, Server } from 'lucide-react'
import { Card, Badge, PageHeader, hubGet, Loading, ErrorBox } from '../components/ui'

interface ProbePoint {
  timestamp: string
  overall: number
  dimensions?: Record<string, number | null>
  probe_details?: Record<string, { available: boolean; score: number | null; detail: string }>
}

interface AlertItem {
  name: string
  severity: string
  detail?: string
  [k: string]: unknown
}

const PROBE_LAYERS = [
  { key: 'l1_process', label: 'L1 进程', color: '#22d3ee' },
  { key: 'l2_dependency', label: 'L2 依赖', color: '#34d399' },
  { key: 'l3_llm_tool', label: 'L3 LLM/工具', color: '#fbbf24' },
  { key: 'l4_business', label: 'L4 业务', color: '#f472b6' },
  { key: 'l5_semantic', label: 'L5 语义', color: '#a78bfa' },
]

function timeLabel(ts: string) {
  try {
    const d = new Date(ts)
    return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch {
    return ts
  }
}

export default function PanoramaHealth() {
  const [points, setPoints] = useState<ProbePoint[]>([])
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      hubGet('/api/health/probe-trend?hours=1').then((r) => {
        const d = r as { points?: ProbePoint[] }
        return Array.isArray(d?.points) ? d.points : []
      }),
      hubGet('/api/observability/alerts').then((r) => {
        const d = r as { groups?: { alerts?: AlertItem[] }[] }
        const groups = Array.isArray(d?.groups) ? d.groups : []
        return groups.flatMap((g) => g.alerts ?? [])
      }),
      hubGet('/api/diagnostics/metrics').catch(() => null),
    ]).then(([p, a, m]) => {
      if (p.status === 'fulfilled') setPoints(p.value ?? [])
      if (a.status === 'fulfilled') setAlerts(a.value ?? [])
      if (m.status === 'fulfilled') setMetrics(m.value as Record<string, unknown> | null)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(() => {
    load()
    // 每 60s 自动刷新（监控场景）
    const timer = setInterval(load, 60000)
    return () => clearInterval(timer)
  }, [])

  // 图表数据：每点 {timestamp, overall, l1..l5}
  const chartData = points.map((p) => {
    const row: Record<string, unknown> = { timestamp: timeLabel(p.timestamp), overall: p.overall }
    for (const layer of PROBE_LAYERS) {
      row[layer.key] = p.dimensions?.[layer.key] ?? null
    }
    return row
  })

  const latest = points.length > 0 ? points[points.length - 1] : null
  const severityColor = (sev: string) => (sev === 'critical' ? 'red' : sev === 'warning' ? 'amber' : sev === 'info' ? 'cyan' : 'slate')

  return (
    <div className="p-6">
      <PageHeader
        title="健康仪表盘"
        description="综合健康度 · 五层探针 · 运行告警（原 / 页面 React 版）"
        actions={
          <button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
            <RefreshCw size={12} /> 刷新
          </button>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading && points.length === 0 ? <Loading /> : (
        <>
          {/* 综合健康度趋势 */}
          <div className="mb-4 grid gap-4 lg:grid-cols-2">
            <Card title="综合健康度与各维度得分趋势">
              <div style={{ width: '100%', height: 250 }}>
                <ResponsiveContainer>
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="overallGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.4} />
                        <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" stroke="#64748b" fontSize={11} />
                    <YAxis domain={[0, 1]} stroke="#64748b" fontSize={11} />
                    <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                    <Area type="monotone" dataKey="overall" name="综合健康" stroke="#22d3ee" fill="url(#overallGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card title="健康评分 + 五层探针（L1~L5）变化">
              <div style={{ width: '100%', height: 250 }}>
                <ResponsiveContainer>
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" stroke="#64748b" fontSize={11} />
                    <YAxis domain={[0, 1]} stroke="#64748b" fontSize={11} />
                    <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    {PROBE_LAYERS.map((l) => (
                      <Line key={l.key} type="monotone" dataKey={l.key} name={l.label} stroke={l.color} dot={false} connectNulls strokeWidth={1.5} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          {/* 探针明细 + 告警 */}
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="五层探针明细">
              {!latest ? (
                <div className="py-6 text-center text-sm text-slate-600">暂无探针数据</div>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                    <span className="text-sm text-slate-300">综合健康度</span>
                    <span className={`font-mono text-lg font-semibold ${(latest.overall ?? 0) >= 0.8 ? 'text-emerald-400' : (latest.overall ?? 0) >= 0.5 ? 'text-amber-400' : 'text-red-400'}`}>
                      {latest.overall != null ? (latest.overall * 100).toFixed(0) + '%' : '-'}
                    </span>
                  </div>
                  {PROBE_LAYERS.map((l) => {
                    const pd = latest.probe_details?.[l.key]
                    const score = latest.dimensions?.[l.key]
                    return (
                      <div key={l.key} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 text-sm text-slate-300">
                            <span className="h-2 w-2 rounded-full" style={{ background: l.color }} />
                            {l.label}
                            {pd && <Badge color={pd.available ? 'green' : 'slate'}>{pd.available ? '可用' : '不可用'}</Badge>}
                          </div>
                          {pd?.detail && <div className="mt-0.5 truncate text-xs text-slate-500" title={pd.detail}>{pd.detail}</div>}
                        </div>
                        <span className="shrink-0 font-mono text-sm text-slate-300">
                          {score != null ? (score * 100).toFixed(0) + '%' : '-'}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </Card>

            <Card title={`运行告警（${alerts.length}）`}>
              {alerts.length === 0 ? (
                <div className="flex flex-col items-center gap-2 py-8 text-slate-600">
                  <Server size={22} />
                  <span className="text-sm">当前无告警</span>
                </div>
              ) : (
                <div className="max-h-[380px] space-y-2 overflow-y-auto">
                  {alerts.map((a, i) => (
                    <div key={i} className="flex items-start justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                      <div className="min-w-0">
                        <div className="text-sm text-slate-200">{String(a.name ?? a.alert_name ?? '')}</div>
                        {a.detail && <div className="mt-0.5 truncate text-xs text-slate-500" title={String(a.detail)}>{String(a.detail)}</div>}
                      </div>
                      <Badge color={severityColor(String(a.severity ?? ''))}>{String(a.severity ?? '?')}</Badge>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
