/**
 * 模块列表 —— 自动发现并列出系统全部模块
 * ------------------------------------------------
 * 数据源：/api/modules/topology（自动拓扑，无需手写清单）
 * 功能：按域（感知/认知/记忆/行动/服务/运维）分组展示模块，
 *       显示状态/类型/路径/危险等级，支持按状态筛选与刷新。
 */
import { useEffect, useMemo, useState } from 'react'
import { Boxes, RefreshCw, Search } from 'lucide-react'
import { Card, Badge, PageHeader, hubGet, Loading, ErrorBox, pickObj } from './components/ui'

interface ModuleNode {
  module_id: string
  name: string
  path?: string
  type?: string
  status?: string
  status_detail?: string
  danger?: string
  actions?: string | string[]
}

interface Domain {
  domain_id: string
  domain_name: string
  icon?: string
  nodes: ModuleNode[]
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

export default function ModuleListPage() {
  const [domains, setDomains] = useState<Domain[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    hubGet('/api/modules/topology').then((r) => {
      const d = pickObj<{ domains?: Domain[] }>(r) ?? {}
      setDomains(Array.isArray(d.domains) ? d.domains : [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  // 全量模块（展平）
  const allModules = useMemo(() => domains.flatMap((dm) => dm.nodes), [domains])

  // 过滤
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return allModules.filter((m) => {
      if (statusFilter && (m.status ?? '') !== statusFilter) return false
      if (q && !`${m.name} ${m.module_id} ${m.path ?? ''}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [allModules, query, statusFilter])

  const healthyCount = allModules.filter((m) => ['healthy', 'running', 'online'].includes(m.status ?? '')).length

  return (
    <div className="p-6">
      <PageHeader
        title="模块列表"
        description={`自动发现全部模块（${domains.length} 个域 / ${allModules.length} 个模块）`}
        actions={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-2 py-1.5">
              <Search size={12} className="text-slate-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索模块…"
                className="w-32 bg-transparent text-xs text-slate-300 placeholder-slate-600 outline-none"
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
            <button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RefreshCw size={12} /> 刷新
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}

      {loading ? <Loading /> : (
        <>
          {/* 概览统计 */}
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-5">
            <Card title="总模块" className="col-span-1"><div className="text-2xl font-semibold text-white">{allModules.length}</div></Card>
            <Card title="健康" className="col-span-1"><div className="text-2xl font-semibold text-emerald-400">{healthyCount}</div></Card>
            <Card title="离线" className="col-span-1"><div className="text-2xl font-semibold text-red-400">{allModules.length - healthyCount}</div></Card>
            <Card title="筛选结果" className="col-span-1"><div className="text-2xl font-semibold text-cyan-400">{filtered.length}</div></Card>
            <Card title="自动发现" className="col-span-1"><div className="text-2xl font-semibold text-slate-300">✓</div></Card>
          </div>

          {/* 域分组展示 */}
          {domains.map((dm) => {
            const nodes = dm.nodes.filter((m) => {
              if (statusFilter && (m.status ?? '') !== statusFilter) return false
              const q = query.trim().toLowerCase()
              if (q && !`${m.name} ${m.module_id}`.toLowerCase().includes(q)) return false
              return true
            })
            if (nodes.length === 0) return null
            return (
              <Card key={dm.domain_id} title={`${dm.icon ?? ''} ${dm.domain_name}（${nodes.length}/${dm.nodes.length}）`} className="mb-4">
                <div className="grid gap-2 md:grid-cols-2">
                  {nodes.map((m) => (
                    <div key={m.module_id} className="flex items-start justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-200">{m.name}</span>
                          <span className="shrink-0 font-mono text-[10px] text-slate-600">{m.module_id}</span>
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                          <span className="rounded bg-slate-800 px-1.5 py-0.5">{TYPE_LABEL[m.type ?? 'module'] ?? m.type}</span>
                          {m.path && <span className="truncate font-mono">{m.path}</span>}
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge color={STATUS_COLOR[m.status ?? ''] ?? 'slate'}>{m.status ?? '?'}</Badge>
                        {m.status_detail && <span className="text-[10px] text-slate-600">{m.status_detail}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            )
          })}

          {filtered.length === 0 && !loading && (
            <div className="py-10 text-center text-sm text-slate-600">无匹配模块</div>
          )}
        </>
      )}
    </div>
  )
}
