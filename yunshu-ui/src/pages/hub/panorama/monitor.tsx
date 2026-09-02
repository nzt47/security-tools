/**
 * 系统监控 —— 模块拓扑 / 健康评分 / 运行状态
 * 数据源：/api/modules/topology、/api/health/dashboard、/api/status
 */
import { useEffect, useState } from 'react'
import { Server, Activity, RefreshCw } from 'lucide-react'
import { Card, StatCard, DataTable, Loading, ErrorBox, Badge, PageHeader, hubGet, pickObj } from '../components/ui'

interface ModuleNode {
  id: string
  name: string
  status?: string
  health?: string
  version?: string
  children?: ModuleNode[]
}

interface HealthItem {
  sensor_name?: string
  description?: string
  severity?: string
  score?: number
}

export default function PanoramaMonitor() {
  const [modules, setModules] = useState<ModuleNode[]>([])
  const [health, setHealth] = useState<HealthItem[]>([])
  const [, setStatus] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      hubGet('/api/modules/topology').then((r) => { const d = pickObj<{ domains?: ModuleNode[] }>(r) ?? {}; const doms = d.domains ?? []; return doms.flatMap((dm: ModuleNode) => (dm.children ?? []).length ? dm.children! : [dm]) }),
      // /api/health/dashboard 返回 {dimensions: {error_rate, response_time, tool_success}, issues, overall_health}
      // 将 dimensions 对象转为评分数组，issues 并入（作为异常项）
      hubGet('/api/health/dashboard').then((r) => {
        const d = pickObj<{ dimensions?: Record<string, number>; issues?: HealthItem[]; overall_health?: number }>(r) ?? {}
        const dims = Object.entries(d.dimensions ?? {}).map(([k, v]) => {
          const num = Number(v) || 0 // NaN → 0（不能写 ??，左侧恒为 number）
          return {
            sensor_name: k, description: k.replace(/_/g, ' '), score: num,
            severity: num < 0.8 ? 'warning' : 'normal',
          }
        })
        return [...dims, ...(Array.isArray(d.issues) ? d.issues : [])]
      }),
      hubGet('/api/status').catch(() => null),
    ]).then(([m, h, s]) => {
      if (m.status === 'fulfilled') setModules(m.value ?? [])
      if (h.status === 'fulfilled') setHealth(h.value ?? [])
      if (s.status === 'fulfilled') setStatus(s.value as Record<string, unknown> | null)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const flatten = (nodes: ModuleNode[]): ModuleNode[] =>
    nodes.flatMap((n) => [n, ...(n.children ? flatten(n.children) : [])])

  const flat = flatten(modules)

  return (
    <div className="p-6">
      <PageHeader
        title="系统监控"
        description="模块运行状态与健康评分"
        actions={
          <button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
            <RefreshCw size={12} /> 刷新
          </button>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="模块总数" value={flat.length} icon={<Server size={16} />} />
            <StatCard
              label="在线模块"
              value={flat.filter((m) => String(m.status ?? m.health ?? '').toLowerCase() !== 'offline').length}
              icon={<Activity size={16} />} color="text-emerald-400"
            />
            <StatCard label="健康检查项" value={health.length} icon={<Activity size={16} />} />
            <StatCard
              label="异常项"
              value={health.filter((h) => h.severity && h.severity !== 'normal').length}
              icon={<Activity size={16} />} color="text-amber-400"
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="模块拓扑">
              <DataTable
                data={flat}
                keyField="id"
                columns={[
                  { key: 'name', title: '模块', render: (r) => <span className="text-slate-200">{String(r.name ?? r.id)}</span> },
                  { key: 'status', title: '状态', render: (r) => (
                    <Badge color={String(r.status ?? r.health) === 'ok' || String(r.status) === 'online' ? 'green' : String(r.status) === 'offline' ? 'red' : 'slate'}>
                      {String(r.status ?? r.health ?? '?')}
                    </Badge>
                  ) },
                ]}
              />
            </Card>

            <Card title="健康评分">
              <DataTable
                data={health}
                keyField="sensor_name"
                columns={[
                  { key: 'description', title: '项目', render: (r) => <span className="text-slate-200">{String(r.description ?? r.sensor_name ?? '')}</span> },
                  { key: 'severity', title: '等级', render: (r) => (
                    <Badge color={r.severity === 'normal' ? 'green' : r.severity === 'warning' ? 'amber' : 'red'}>
                      {r.severity ?? '-'}
                    </Badge>
                  ) },
                  { key: 'score', title: '评分', render: (r) => (r.score != null ? <span className="font-mono">{Number(r.score).toFixed(2)}</span> : '-') },
                ]}
              />
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
