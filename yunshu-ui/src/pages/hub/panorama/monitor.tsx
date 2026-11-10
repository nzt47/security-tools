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
  /** declared = modules_registry.DOMAINS 手写声明；discovered = 包自声明后自动发现 */
  origin?: string
}

/** 「未登记」：声明了模块元数据、但没有合法 status_source 的包（不生成节点、不参与健康分） */
interface UnregisteredModule {
  package?: string
  name?: string
  reason?: string
}

interface Domain {
  domain_id: string
  domain_name: string
  icon?: string
  nodes: ModuleNode[]
}

/** /api/modules/topology 的业务载荷：{generated_at, overall_health, domains, autodiscovery, unregistered} */
interface ModulesTopology {
  generated_at?: string
  overall_health?: number | null
  domains?: Domain[]
  /** 自动发现读数（2026-11-11）：开关 / 扫描包数 / 已发现 / 未登记 */
  autodiscovery?: {
    enabled?: boolean
    scanned_packages?: number
    discovered?: number
    unregistered?: number
  }
  unregistered?: UnregisteredModule[]
}

interface HealthItem {
  sensor_name?: string
  description?: string
  /** normal / warning / no_data（no_data = 该层探针没有数据，**不当 0 也不当满分**） */
  severity?: string
  score?: number | null
}

/** /api/health/dashboard 的业务载荷：{dimensions, layers, issues, overall_health} */
interface HealthDashboard {
  /** 五层探针分数；不可用的层是 null（不是 0） */
  dimensions?: Record<string, number | null>
  /** 每层 available / detail / score（2026-11-10 新增） */
  layers?: Record<string, { score?: number | null; available?: boolean; detail?: string }>
  issues?: HealthItem[]
  overall_health?: number | null
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
  const [unregistered, setUnregistered] = useState<UnregisteredModule[]>([])
  const [autodiscovery, setAutodiscovery] = useState<ModulesTopology['autodiscovery']>(undefined)
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
        // 【2026-11-11】「未登记」单列：声明了是模块却没声明状态源 ⇒ 不生成节点、
        // 不参与健康分；这里只做披露，绝不把它们并进 domains（否则健康分会虚高/虚低）。
        setUnregistered(d && Array.isArray(d.unregistered) ? d.unregistered : [])
        setAutodiscovery(d && d.autodiscovery ? d.autodiscovery : undefined)
      } else {
        setError('模块拓扑加载失败：' + String(m.reason))
        setDomains([])
        setUnregistered([])
        setAutodiscovery(undefined)
      }
      if (h.status === 'fulfilled') {
        const d = h.value ?? {}
        // 【2026-11-10】优先用 layers（五层探针的真实 available/detail）：
        //   过去的 3 项是 HealthScore 的默认常量（恒 1.0），现在是测量结果；
        //   score=null 的层标 no_data —— 不折算成 0（那会变成假告警），也不当满分。
        const layers = d.layers ?? {}
        const rows = Object.keys(layers).length
          ? Object.entries(layers).map(([k, v]) => {
              const score = v?.score == null ? null : Number(v.score)
              return {
                sensor_name: k,
                description: (v?.detail as string) || k.replace(/_/g, ' '),
                score,
                severity: score == null ? 'no_data' : score < 0.8 ? 'warning' : 'normal',
              }
            })
          : Object.entries(d.dimensions ?? {}).map(([k, v]) => {
              const score = v == null ? null : Number(v)
              return {
                sensor_name: k,
                description: k.replace(/_/g, ' '),
                score,
                severity: score == null ? 'no_data' : score < 0.8 ? 'warning' : 'normal',
              }
            })
        setHealth([...rows, ...(Array.isArray(d.issues) ? d.issues : [])])
      }
      setLoading(false)
    })
  }

  useEffect(load, [])

  const allModules = useMemo(() => domains.flatMap((d) => d.nodes ?? []), [domains])

  // 自动发现并入的节点数（与手写节点同权参与健康分；这里只用于在页面上说清来源）
  const discoveredCount = allModules.filter((m) => m.origin === 'discovered').length

  const online = allModules.filter((m) => ONLINE_STATUS.has(m.status ?? '')).length
  const offline = allModules.filter((m) => m.status === 'offline').length
  const disabled = allModules.filter((m) => m.status === 'disabled').length
  // 其余状态（warning/degraded/未知）单列，保证 在线 + 离线 + 未启用 + 其他 = 总数
  const other = allModules.length - online - offline - disabled
  // 「健康检查项」只数真正的检查行（探针层，带 severity）；issues 文本行照旧上表，但不计入项数。
  const healthChecks = health.filter((h) => h.severity).length
  // 「异常项」只数真正的低分（warning）：no_data 是「没有数据」，不是异常（L5 口径）。
  const healthIssues = health.filter((h) => h.severity === 'warning').length

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
        description={'运行时视角：模块状态、健康评分与按域明细（' + domains.length + ' 个域 / '
          + allModules.length + ' 个模块'
          + (discoveredCount > 0 ? '，其中 ' + discoveredCount + ' 个自动发现' : '')
          + (unregistered.length > 0 ? '；' + unregistered.length + ' 个未登记' : '') + '）'}
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
            <StatCard label="健康检查项" value={healthChecks} icon={<Activity size={16} />} />
            <StatCard label="异常项" value={healthIssues} icon={<Activity size={16} />} color="text-amber-400" />
          </div>
          {other > 0 && (
            <p className="mb-4 text-[11px] text-amber-400">
              另有 {other} 个模块处于其他状态（如 warning / degraded），未计入在线 / 离线 / 未启用。
            </p>
          )}

          {/* 【2026-11-11】「未登记」单列：以前这些包**根本不会出现在这个页面上**
              （DOMAINS 是手写清单，没人加行 = 一个节点都没有），而一个人不知道
              "它没上图"这件事本身不会被任何东西报出来。现在如实列出来：
              它们只是缺一行 MODULE_STATUS_SOURCE —— 不生成节点、不参与健康分。 */}
          {unregistered.length > 0 && (
            <div className="mb-4" data-testid="module-unregistered">
            <Card title={'未登记模块（' + unregistered.length + '）'}>
              <p className="mb-2 text-[11px] text-slate-400">
                这些包声明了「我是个功能面」，但<strong className="text-slate-300">没有声明状态源</strong>
                （<code className="text-cyan-300">MODULE_STATUS_SOURCE</code>）⇒ 不生成节点、
                <strong className="text-slate-200">不参与健康分</strong>。
                补一行声明即可自动上图（无需改手写清单）。
              </p>
              <div className="grid gap-2 md:grid-cols-2" data-testid="unregistered-list">
                {unregistered.map((u) => (
                  <div key={u.package ?? u.name} className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm text-slate-300">{u.name ?? u.package}</span>
                      <span className="shrink-0 font-mono text-[10px] text-slate-600">{u.package}</span>
                    </div>
                    {u.reason && <div className="mt-1 text-[10px] text-slate-500">{u.reason}</div>}
                  </div>
                ))}
              </div>
            </Card>
            </div>
          )}

          {autodiscovery?.enabled === false && (
            <p className="mb-4 text-[11px] text-slate-500" data-testid="autodiscovery-off">
              模块自动发现已关闭（CP_MODULE_AUTODISCOVERY=0）⇒ 拓扑只含手写清单里的节点。
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
                    <Badge color={r.severity === 'normal' ? 'green' : r.severity === 'warning' ? 'amber' : 'slate'}>
                      {r.severity === 'no_data' ? '无数据' : (r.severity ?? '-')}
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
                          {m.origin === 'discovered' && (
                            <span
                              className="shrink-0 rounded-sm bg-cyan-950 px-1.5 py-0.5 text-[9px] text-cyan-300"
                              data-testid={'origin-badge-' + m.module_id}
                              title={'该节点由包自己声明（agent/<pkg>/__init__.py 的 MODULE_STATUS_SOURCE），不是手写清单里的一行'}
                            >
                              自动发现
                            </span>
                          )}
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
