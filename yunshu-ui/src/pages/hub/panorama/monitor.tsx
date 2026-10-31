/**
 * 系统监控 —— 全景看板下「运行时视角」的统一页面（2026-10-15 合并「总览 / 模块明细」）
 * ------------------------------------------------------------------
 * 【为什么合并】本页与「模块列表」原本都读 /api/modules/topology（前端仅这两个消费方），
 * 是同一份数据的两个入口 —— 正是本仓纪律要避免的「双入口」。2026-10-14 先合并为页内两
 * Tab，但两个 Tab 各自取数、字段口径也不一致，实测对不上：
 *   · 总览读的是 domains[].children（该字段不存在）⇒ 兜底把 6 个「域对象」当模块列出；
 *   · 模块明细读 domains[].nodes ⇒ 32 个模块；
 *   · 「离线」用「总数 − 健康数」计算，把 1 个「未启用」也算了进去（18 而非 17）。
 * 本版合为一页：同一份数据、同一套口径，摘要 / 健康评分 / 按域明细同屏，
 * 不再有第二个入口，也不会再出现两处数字互相矛盾。
 *
 * 数据源：/api/modules/topology（模块树）、/api/health/dashboard（健康评分）。
 */
import { useEffect, useMemo, useState } from 'react'
import { Activity, RefreshCw, Search, Server } from 'lucide-react'
import { Card, StatCard, DataTable, Loading, ErrorBox, Badge, PageHeader } from '../components/ui'
// 2026-10-18：节点下钻 + 干预（/api/modules/<id>/detail 与 /actions）此前零入口
import ModuleDetail from './ModuleDetail'
import { getEnvelope } from '@/api/envelope'
import { HEALTH_DASHBOARD, MODULES_TOPOLOGY } from '@/api/endpoints'

interface ModuleNode {
  module_id: string
  name: string
  path?: string
  type?: string
  status?: string
  status_detail?: string
  danger?: string
}

interface Domain {
  domain_id: string
  domain_name: string
  icon?: string
  nodes: ModuleNode[]
}

/** /api/modules/topology 的业务载荷：{generated_at, overall_health, domains} */
interface ModulesTopology {
  generated_at?: string
  overall_health?: number | null
  domains?: Domain[]
}

interface HealthItem {
  sensor_name?: string
  description?: string
  severity?: string
  score?: number
}

/** /api/health/dashboard 的业务载荷：{dimensions, issues, overall_health} */
interface HealthDashboard {
  dimensions?: Record<string, number>
  issues?: HealthItem[]
  overall_health?: number
}

const STATUS_COLOR: Record<string, 'green' | 'red' | 'amber' | 'slate'> = {
  healthy: 'green',
  running: 'green',
  online: 'green',
  offline: 'red',
  disabled: 'slate',
  warning: 'amber',
  degraded: 'amber',
}

const TYPE_LABEL: Record<string, string> = {
  sensor: '传感器', service: '服务', module: '模块', config: '配置',
}

/** 「在线」口径：与明细筛选/徽章同一套判定，避免两处各算各的 */
const ONLINE_STATUS: ReadonlySet<string> = new Set(['healthy', 'running', 'online'])

export default function PanoramaMonitor() {
  const [domains, setDomains] = useState<Domain[]>([])
  const [health, setHealth] = useState<HealthItem[]>([])
  const [overallHealth, setOverallHealth] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  /** 展开详情/干预的模块（同一时刻只开一个，避免一屏几十个面板同时拉详情） */
  const [openModule, setOpenModule] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    // 两个端点各自独立：模块树失败要上屏（它是本页主体），健康评分失败不阻断模块明细
    Promise.allSettled([
      getEnvelope<ModulesTopology | null>(MODULES_TOPOLOGY),
      getEnvelope<HealthDashboard | null>(HEALTH_DASHBOARD),
    ]).then(([m, h]) => {
      if (m.status === 'fulfilled') {
        const d = m.value
        setDomains(d && Array.isArray(d.domains) ? d.domains : [])
        setOverallHealth(d && typeof d.overall_health === 'number' ? d.overall_health : null)
      } else {
        setError('模块拓扑加载失败：' + String(m.reason))
        setDomains([])
      }
      if (h.status === 'fulfilled') {
        const d = h.value ?? {}
        const dims = Object.entries(d.dimensions ?? {}).map(([k, v]) => {
          const num = Number(v) || 0 // NaN → 0（不能写 ??，左侧恒为 number）
          return {
            sensor_name: k,
            description: k.replace(/_/g, ' '),
            score: num,
            severity: num < 0.8 ? 'warning' : 'normal',
          }
        })
        setHealth([...dims, ...(Array.isArray(d.issues) ? d.issues : [])])
      }
      setLoading(false)
    })
  }

  useEffect(load, [])

  const allModules = useMemo(() => domains.flatMap((d) => d.nodes ?? []), [domains])

  const online = allModules.filter((m) => ONLINE_STATUS.has(m.status ?? '')).length
  const offline = allModules.filter((m) => m.status === 'offline').length
  const disabled = allModules.filter((m) => m.status === 'disabled').length
  // 其余状态（warning/degraded/未知）单列，保证 在线 + 离线 + 未启用 + 其他 = 总数
  const other = allModules.length - online - offline - disabled
  const healthIssues = health.filter((h) => h.severity && h.severity !== 'normal').length

  const filteredDomains = useMemo(() => {
    const q = query.trim().toLowerCase()
    return domains
      .map((d) => ({
        ...d,
        total: (d.nodes ?? []).length,
        nodes: (d.nodes ?? []).filter((m) => {
          if (statusFilter && (m.status ?? '') !== statusFilter) return false
          if (q && ![m.name, m.module_id, m.path ?? ''].join(' ').toLowerCase().includes(q)) return false
          return true
        }),
      }))
      .filter((d) => d.nodes.length > 0)
  }, [domains, query, statusFilter])

  const filteredCount = filteredDomains.reduce((n, d) => n + d.nodes.length, 0)

  return (
    <div className="p-6">
      <PageHeader
        title="系统监控"
        description={'运行时视角：模块状态、健康评分与按域明细（' + domains.length + ' 个域 / ' + allModules.length + ' 个模块）'}
        actions={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-2 py-1.5">
              <Search size={12} className="text-slate-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索模块…"
                className="w-32 bg-transparent text-xs text-slate-300 placeholder-slate-600 outline-hidden"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-300"
            >
              <option value="">全部状态</option>
              <option value="healthy">健康</option>
              <option value="offline">离线</option>
              <option value="disabled">未启用</option>
            </select>
            <button
              onClick={load}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
            >
              <RefreshCw size={12} /> 刷新
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}

      {/* 【2026-11-01】读图须知：三个服务节点同源，避免把"一个低分"看成"三处故障" */}
      <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2 text-[11px] text-slate-400">
        <span className="text-slate-300">读图提示：</span>
        服务层的「Web 主服务」「API 网关」与运维层的「健康系统」都取<strong className="text-slate-200">同一个</strong>五层探针分数
        （<code className="text-cyan-300">get_probe_overview</code>），所以一条低分会在这三处同时变黄 ——
        这不是三个独立故障；看「健康仪表盘」里的分层明细（L1 内存 / L5 反馈）才知道是哪一层拖的。
      </div>

      {loading ? <Loading /> : (
        <>
          {/* ── 摘要：模块状态 + 健康检查（与下方明细共用同一份数据）── */}
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <StatCard label="模块总数" value={allModules.length} icon={<Server size={16} />} />
            <StatCard label="在线" value={online} icon={<Activity size={16} />} color="text-emerald-400" />
            <StatCard label="离线" value={offline} icon={<Activity size={16} />} color="text-rose-400" />
            <StatCard label="未启用" value={disabled} icon={<Activity size={16} />} color="text-slate-400" />
            <StatCard label="健康检查项" value={health.length} icon={<Activity size={16} />} />
            <StatCard label="异常项" value={healthIssues} icon={<Activity size={16} />} color="text-amber-400" />
          </div>
          {other > 0 && (
            <p className="mb-4 text-[11px] text-amber-400">
              另有 {other} 个模块处于其他状态（如 warning / degraded），未计入在线 / 离线 / 未启用。
            </p>
          )}

          {/* ── 健康评分 ── */}
          <Card
            title={overallHealth != null ? '健康评分（整体健康度 ' + overallHealth + '）' : '健康评分'}
            className="mb-4"
          >
            <DataTable
              data={health}
              keyField="sensor_name"
              columns={[
                { key: 'description', title: '项目', render: (r) => <span className="text-slate-200">{String(r.description ?? r.sensor_name ?? '')}</span> },
                {
                  key: 'severity', title: '等级', render: (r) => (
                    <Badge color={r.severity === 'normal' ? 'green' : r.severity === 'warning' ? 'amber' : 'red'}>
                      {r.severity ?? '-'}
                    </Badge>
                  ),
                },
                { key: 'score', title: '评分', render: (r) => (r.score != null ? <span className="font-mono">{Number(r.score).toFixed(2)}</span> : '-') },
              ]}
            />
          </Card>

          {/* ── 模块明细（按域分组；搜索/筛选共用页头输入）── */}
          <div className="mb-2 flex items-center gap-2">
            <h2 className="text-sm font-medium text-slate-200">模块明细</h2>
            <span className="text-[11px] text-slate-500">
              {query.trim() || statusFilter ? '筛选 ' + filteredCount + ' / ' + allModules.length : '全部 ' + allModules.length}
            </span>
          </div>

          {filteredDomains.map((dm) => (
            <Card
              key={dm.domain_id}
              title={(dm.icon ? dm.icon + ' ' : '') + dm.domain_name + '（' + dm.nodes.length + '/' + dm.total + '）'}
              className="mb-4"
            >
              <div className="grid gap-2 md:grid-cols-2">
                {dm.nodes.map((m) => (
                  <div key={m.module_id} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-200">{m.name}</span>
                          <span className="shrink-0 font-mono text-[10px] text-slate-600">{m.module_id}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                          <span className="rounded-sm bg-slate-800 px-1.5 py-0.5">{TYPE_LABEL[m.type ?? 'module'] ?? m.type}</span>
                          {m.path && <span className="truncate font-mono">{m.path}</span>}
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge color={STATUS_COLOR[m.status ?? ''] ?? 'slate'}>{m.status ?? '?'}</Badge>
                        {m.status_detail && <span className="text-[10px] text-slate-600">{m.status_detail}</span>}
                        <button
                          type="button"
                          onClick={() => setOpenModule(openModule === m.module_id ? '' : m.module_id)}
                          className="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-800"
                          aria-label={'模块详情 ' + m.module_id}
                        >
                          {openModule === m.module_id ? '收起' : '详情 / 干预'}
                        </button>
                      </div>
                    </div>
                    {openModule === m.module_id && (
                      <ModuleDetail moduleId={m.module_id} onClose={() => setOpenModule('')} />
                    )}
                  </div>
                ))}
              </div>
            </Card>
          ))}

          {filteredCount === 0 && (
            <div className="py-10 text-center text-sm text-slate-600">
              {allModules.length === 0 ? '暂无模块数据' : '无匹配模块'}
            </div>
          )}
        </>
      )}
    </div>
  )
}
