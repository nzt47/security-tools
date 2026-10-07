/**
 * 路由覆盖 —— 把「450 条后端路由谁进了 UI」呈现出来（2026-10-07）。
 *
 * 数据源：GET /api/audit/route-ui-coverage（读提交产物 reports/route_ui_coverage.json）。
 * 【本页是「宿主功能零清单进导航」的第二个实例】它只是一个
 * src/features/<key>/index.tsx（export default + manifest），由 featureRegistry 自动挂上。
 *
 * 【为什么它不自己跑扫描】全树扫描（路由 AST + 数百前端文件）不适合放在 HTTP 热路径；
 * 产物由 scripts/audit/route_ui_coverage.py 生成，守卫用 --check 钉住新鲜度，
 * 并用 reports/route_ui_unreferenced_ceiling.json 做「未分类活体路由只允许收缩」的门禁。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Route as RouteIcon, RefreshCw, Search } from 'lucide-react'
import { request } from '@/lib/apiClient'
import { ROUTE_UI_COVERAGE } from '@/api/endpoints'

export const manifest = { key: 'route-coverage', label: '路由覆盖', icon: RouteIcon, order: 910 }

interface Entry {
  path: string
  norm: string
  methods: string[]
  category: string
  live: boolean
  ui_refs: string[]
  non_ui_refs: string[]
  where: string[]
}
interface Payload {
  generated_at: string
  totals: Record<string, unknown>
  frontend_endpoints: number
  frontend_unmatched: string[]
  entries: Entry[]
}

const CAT_LABEL: Record<string, string> = {
  ui: '已进 UI',
  cli_script: '仅 CLI·脚本',
  runtime_only: '仅运行时',
  unreferenced: '未分类',
}
const CAT_CLASS: Record<string, string> = {
  ui: 'text-emerald-400',
  cli_script: 'text-cyan-400',
  runtime_only: 'text-amber-400',
  unreferenced: 'text-slate-500',
}

export default function RouteCoveragePage() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cat, setCat] = useState<string>('all')
  const [liveOnly, setLiveOnly] = useState(false)
  const [q, setQ] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    request<Payload>(ROUTE_UI_COVERAGE)
      .then((d) => setData(d && Array.isArray(d.entries) ? d : null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const rows = useMemo(() => {
    const list = data?.entries ?? []
    const needle = q.trim().toLowerCase()
    return list.filter((e) =>
      (cat === 'all' || e.category === cat)
      && (!liveOnly || e.live)
      && (!needle || e.path.toLowerCase().includes(needle)),
    )
  }, [data, cat, liveOnly, q])

  const t = (data?.totals ?? {}) as Record<string, number>
  const liveByCat = (t.live_by_category ?? {}) as Record<string, number>

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
            <RouteIcon size={18} className="text-cyan-400" /> 路由覆盖
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            后端路由 → 前端 UI 的机械盘点。数据源为提交产物，生成时间 {data?.generated_at ?? '—'}。
          </p>
        </div>
        <button onClick={load}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
          <RefreshCw size={12} /> 重新读取
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-2 text-sm text-red-300">读取失败：{error}</div>}
      {loading && <div className="text-sm text-slate-500">加载中…</div>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ['静态路由', t.routes], ['活体', t.live], ['死副本', t.dead_copy], ['前端端点', data?.frontend_endpoints],
        ].map(([label, v]) => (
          <div key={String(label)} className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3">
            <div className="text-xs text-slate-500">{label}</div>
            <div className="mt-1 text-xl font-semibold text-slate-100">{v ?? '—'}</div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {(['all', 'ui', 'cli_script', 'runtime_only', 'unreferenced'] as const).map((c) => (
          <button key={c} onClick={() => setCat(c)}
            className={'rounded-lg border px-3 py-1.5 ' + (cat === c ? 'border-cyan-700 bg-cyan-950/40 text-cyan-300' : 'border-slate-700 text-slate-300 hover:bg-slate-800')}>
            {c === 'all' ? '全部' : CAT_LABEL[c]}
            {c !== 'all' && <span className="ml-1 text-slate-500">活体 {liveByCat[c] ?? 0} / 总 {t[c] ?? 0}</span>}
          </button>
        ))}
        <label className="ml-2 flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={liveOnly} onChange={(e) => setLiveOnly(e.target.checked)} /> 只看活体
        </label>
        <span className="ml-auto flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-1">
          <Search size={12} className="text-slate-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="按路径过滤"
            className="w-48 bg-transparent text-xs text-slate-200 outline-none" />
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-900/60 text-slate-400">
            <tr>
              <th className="px-3 py-2">路径</th>
              <th className="px-3 py-2">方法</th>
              <th className="px-3 py-2">分类</th>
              <th className="px-3 py-2">活体</th>
              <th className="px-3 py-2">引用</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 400).map((e) => (
              <tr key={e.path} className="border-t border-slate-800/60">
                <td className="px-3 py-1.5 font-mono text-slate-200">{e.path}</td>
                <td className="px-3 py-1.5 text-slate-500">{(e.methods ?? []).join(',')}</td>
                <td className={'px-3 py-1.5 ' + (CAT_CLASS[e.category] ?? '')}>{CAT_LABEL[e.category] ?? e.category}</td>
                <td className="px-3 py-1.5">{e.live ? '是' : <span className="text-slate-600">死副本</span>}</td>
                <td className="px-3 py-1.5 text-slate-500">{(e.ui_refs ?? []).length} 前端 / {(e.non_ui_refs ?? []).length} 非前端</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > 400 && <div className="px-3 py-2 text-xs text-slate-500">仅显示前 400 条（共 {rows.length} 条），请用过滤缩小范围。</div>}
      </div>
    </div>
  )
}
