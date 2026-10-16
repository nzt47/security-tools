/**
 * 路由覆盖 —— 治理面板子项（UI IA 整合阶段 2）
 * ------------------------------------------------------------------
 * 原为 `src/features/route-coverage/index.tsx`（自动发现项「路由覆盖」，顶层）。
 * 它的数据源是**构建期产物**（提交物 + 新鲜度守卫 + 「未分类活体路由只允许收缩」的棘轮），
 * 与全景看板的运行时运维视图语义不匹配；现按方案 §4.4 归入同类语义的「治理面板」，
 * 与「能力图谱 / 审计导出」并列，并删除原自动发现 manifest（禁止双入口）。
 *
 * 数据源：GET /api/audit/route-ui-coverage（读提交产物 reports/route_ui_coverage.json）。
 *
 * 【为什么它不自己跑扫描】全树扫描（路由 AST + 数百前端文件）不适合放在 HTTP 热路径；
 * 产物由 scripts/audit/route_ui_coverage.py 生成，守卫用 --check 钉住新鲜度，
 * 并用 reports/route_ui_unreferenced_ceiling.json 做「未分类活体路由只允许收缩」的门禁。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Route as RouteIcon, RefreshCw, Search } from 'lucide-react'
// 该端点走 v2 统一信封（后端 _ok(payload)）⇒ 必须显式解包；
// 直接用 request()（返回原始体、不拆信封）会在 {code,data,message} 上读 entries 而**静默拿到空**。
import { getEnvelope } from '@/api/envelope'
import { ROUTE_UI_COVERAGE } from '@/api/endpoints'

interface Entry {
  path: string
  norm: string
  methods: string[]
  category: string
  live: boolean
  /** 该路径是否还存在未接线定义（死副本）；与 live 正交：活体在 plugins/x.py、死副本在未接线的 agent/server_routes/y.py */
  dead_copy?: boolean
  /** 未接线的定义位点（file:line），用于说明死副本在哪 */
  dead_defs?: string[]
  ui_refs: string[]
  non_ui_refs: string[]
  where: string[]
  /** 人工意图标注：should_be_in_ui / should_not_be_in_ui / unclassified */
  intent?: string
  intent_area?: string
  /** 该进 UI 却没进（产物已按 live + category 判定） */
  ui_pending?: boolean
  /** 一句话说明：这个路由做什么（处理函数 docstring 首行） */
  description?: string
  /** handler = 处理函数自述；module = 处理函数没写、退到模块首行（非该路由专述） */
  description_source?: string
  /** 人工意图标注的理由（config/route_ui_intent.json 的 note） */
  intent_note?: string
}
interface Payload {
  generated_at: string
  totals: Record<string, unknown>
  frontend_endpoints: number
  frontend_unmatched: string[]
  /** 该进 UI 却没进的路由（服务端按意图标注 + 机械分类算出） */
  pending_ui?: unknown[]
  entries: Entry[]
}

const CAT_LABEL: Record<string, string> = {
  ui: '已进 UI',
  cli_script: '仅 CLI·脚本',
  runtime_only: '仅运行时',
  unreferenced: '未接线（无引用）',
}
/** 分类按钮/单元格的悬浮说明：把机械口径讲清楚，避免被读成"待人工分类" */
const CAT_TIP: Record<string, string> = {
  ui: '有前端引用：这条路由已经被页面调用',
  cli_script: '没有前端引用，但被 CLI / 脚本引用',
  runtime_only: '没有前端引用，但被运行期代码引用（如模块聚合 provider）',
  unreferenced:
    '全仓既没有前端引用，也没有 CLI/脚本/运行时引用。这是机械口径，不代表"待人工分类"；' +
    '要离开这一档，需把路由接进 UI/CLI，或用「意图」标注它的归属。',
}
const CAT_CLASS: Record<string, string> = {
  ui: 'text-emerald-400',
  cli_script: 'text-cyan-400',
  runtime_only: 'text-amber-400',
  unreferenced: 'text-slate-500',
}
const INTENT_LABEL: Record<string, string> = {
  should_be_in_ui: '该进 UI',
  should_not_be_in_ui: '不该进',
  unclassified: '未标注',
}
const INTENT_CLASS: Record<string, string> = {
  should_be_in_ui: 'text-amber-300',
  should_not_be_in_ui: 'text-slate-500',
  unclassified: 'text-slate-600',
}

export default function RouteCoveragePanel() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [cat, setCat] = useState<string>('all')
  const [liveOnly, setLiveOnly] = useState(false)
  const [pendingOnly, setPendingOnly] = useState(false)
  const [q, setQ] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    getEnvelope<Payload>(ROUTE_UI_COVERAGE)
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
      && (!liveOnly || !e.dead_copy)
      && (!pendingOnly || e.ui_pending)
      && (!needle || e.path.toLowerCase().includes(needle)),
    )
  }, [data, cat, liveOnly, pendingOnly, q])

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
          <p
            className="mt-1 max-w-3xl text-[11px] leading-relaxed text-slate-500"
            data-testid="route-coverage-scope-note"
          >
            口径：每条 = 一个归一化路径（&lt;id&gt; / &lt;int:id&gt; 折成 *），不是运行期 url_map 的规则数；
            扫描范围含 agent / plugins / sensor / cognitive / memory，因此也会计入其它 Flask app
            （如 sensor_server）的装饰器，而动态注册（如 /api/open/*）与 Flask 静态路由不在其中；
            判活 = 该路径存在任一已接线定义，含死副本 = 还存在未接线定义。
          </p>
        </div>
        <button onClick={load}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
          <RefreshCw size={12} /> 重新读取
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-2 text-sm text-red-300">读取失败：{error}</div>}
      {loading && <div className="text-sm text-slate-500">加载中…</div>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          ['静态路由(路径)', t.routes], ['活体', t.live], ['含死副本', t.dead_copy], ['前端端点', data?.frontend_endpoints],
          ['该进却未进', (data?.pending_ui ?? []).length],
        ].map(([label, v]) => (
          <div key={String(label)} className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3">
            <div className="text-xs text-slate-500">{label}</div>
            <div className="mt-1 text-xl font-semibold text-slate-100">{v ?? '—'}</div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {(['all', 'ui', 'cli_script', 'runtime_only', 'unreferenced'] as const).map((c) => (
          <button key={c} onClick={() => setCat(c)} title={c === 'all' ? undefined : CAT_TIP[c]}
            className={'rounded-lg border px-3 py-1.5 ' + (cat === c ? 'border-cyan-700 bg-cyan-950/40 text-cyan-300' : 'border-slate-700 text-slate-300 hover:bg-slate-800')}>
            {c === 'all' ? '全部' : CAT_LABEL[c]}
            {c !== 'all' && <span className="ml-1 text-slate-500">活体 {liveByCat[c] ?? 0} / 总 {t[c] ?? 0}</span>}
          </button>
        ))}
        <button onClick={() => setPendingOnly((v) => !v)} data-testid="route-pending-filter"
          className={'ml-2 rounded-lg border px-3 py-1.5 ' + (pendingOnly ? 'border-amber-700 bg-amber-950/40 text-amber-300' : 'border-slate-700 text-slate-300 hover:bg-slate-800')}>
          该进却未进 <span className="ml-1 text-slate-500">{(data?.pending_ui ?? []).length}</span>
        </button>
        <label className="ml-2 flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={liveOnly} onChange={(e) => setLiveOnly(e.target.checked)} /> 只看无死副本
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
              <th className="px-3 py-2">说明</th>
              <th className="px-3 py-2">方法</th>
              <th className="px-3 py-2">分类</th>
              <th className="px-3 py-2">意图</th>
              <th className="px-3 py-2">活体 / 死副本</th>
              <th className="px-3 py-2">引用</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.path} className="border-t border-slate-800/60">
                <td className="px-3 py-1.5 font-mono text-slate-200">{e.path}</td>
                <td className="max-w-[380px] px-3 py-1.5 text-slate-400" data-testid={`route-desc-${e.norm}`}>
                  {e.description ? (
                    <>
                      <span title={e.description}>{e.description}</span>
                      {e.description_source === 'module' && (
                        <span className="ml-1 rounded bg-slate-800 px-1 text-[10px] text-slate-500"
                          title="该路由处理函数未写说明，此处取所在模块的说明">模块</span>
                      )}
                      {(e.where ?? [])[0] && (
                        <span className="ml-1 font-mono text-[10px] text-slate-600" title={(e.where ?? []).join('\n')}>
                          {(e.where ?? [])[0]}
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="text-slate-600">—</span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-slate-500">{(e.methods ?? []).join(',')}</td>
                <td className={'px-3 py-1.5 ' + (CAT_CLASS[e.category] ?? '')} title={CAT_TIP[e.category]}>{CAT_LABEL[e.category] ?? e.category}</td>
                <td className={'px-3 py-1.5 ' + (INTENT_CLASS[e.intent ?? 'unclassified'] ?? '')} data-testid={`route-intent-${e.norm}`} title={e.intent_note || undefined}>
                  {INTENT_LABEL[e.intent ?? 'unclassified'] ?? e.intent}
                  {e.intent_area && <span className="ml-1 text-[10px] text-slate-500">· {e.intent_area}</span>}
                  {e.ui_pending && <span className="ml-1 rounded bg-amber-950/50 px-1 text-[10px] text-amber-300">待办</span>}
                </td>
                <td className="px-3 py-1.5" data-testid={'route-live-' + e.norm}>
                  {e.dead_copy
                    ? <span className="text-amber-400" title={(e.dead_defs ?? []).join('\n')}>含死副本</span>
                    : (e.live ? '是' : <span className="text-slate-600">死副本</span>)}
                </td>
                <td className="px-3 py-1.5 text-slate-500">{(e.ui_refs ?? []).length} 前端 / {(e.non_ui_refs ?? []).length} 非前端</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > 0 && <div className="px-3 py-2 text-xs text-slate-500">共 {rows.length} 条（全部显示）。</div>}
      </div>
    </div>
  )
}
