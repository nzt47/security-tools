/**
 * 工具集 —— 按【四能力平面】分组的工具配置 / 启停
 *
 * 数据源：
 *   - `/api/tools/config`        工具列表 + 启用状态 + 调用次数（plugins/skills.py）
 *   - `/api/tools/toggle`        启停
 *   - `/api/agent-lines/planes`  治理元数据（plane/effect/risk/needs_approval）+ 平面分类法
 *                               + 每行 `callability`（「可被 LLM 调用」统一标注）与
 *                                 `callability_marks` / `callability_note`（三档标识图例）
 *
 * 【为什么工具行上要标"可调用性"】
 *   plane/effect/risk 回答的是"这工具能造成多大后果"，回答不了"模型到底能不能自己发起调用"：
 *   后者由声明 llm_callable / callable_mode / schema / 执行器 / 权限策略**五条共同**决定。
 *   三档标识（✅ 可调用 / ⚠️ 条件可调用 / ❌ 不可调用）与判定同源，直接取自后端，
 *   本页不重算 —— 悬浮说明给出 reason / conditions / notes / 权限 / 执行器。
 *   【退化】`callability` 为 `{}`（旧后端或清单不可用）时不显示徽章，界面与加标注之前一致。
 *
 * 【为什么要按平面分组】
 *   旧的"分类"是学科轴（web/file/code/system…），它回答了"这工具是干什么的"，
 *   但回答不了"它能造成多大后果"。四能力平面是**治理轴**：
 *     resident 常驻 / perceive 感知 / act 行动 / govern 治理（改自身能力集 = 审批边界）
 *   所以本页同时展示 effect（效果上限）与 risk（风险），并把 govern 平面单独置于末尾
 *   并标注"需审批"，让人一眼看见哪些工具能改变云枢自己。
 *
 * 两个接口任一失败都降级：没有治理元数据时只显示旧口径（分类 + 启停），不白屏。
 */
import { useEffect, useMemo, useState } from 'react'
import { Power, ShieldAlert } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, CallabilityBadge, PageHeader, hubGet, hubPost, pickList } from '../components/ui'
import { callabilityCounts, callabilityLegend, hasCallabilityMark, type CallabilityInfo } from '@/lib/callability'

interface ToolItem {
  name: string
  description?: string
  enabled?: boolean
  category?: string
  call_count?: number
  [k: string]: unknown
}

interface MetaRow {
  name: string
  plane?: string
  effect?: string
  risk?: string
  tags?: string[]
  category?: string
  needs_approval?: boolean
  internal?: boolean
  /** 该工具「可被 LLM 调用」的统一标注（可能为 `{}` = 清单不可用） */
  callability?: CallabilityInfo
}

interface PlaneDef {
  key: string
  label: string
  hint: string
  count: number
}

/** 平面展示顺序：常驻 → 感知 → 行动 → 治理；未登记归入"未声明" */
const PLANE_ORDER = ['resident', 'perceive', 'act', 'govern'] as const
const PLANE_STYLE: Record<string, { badge: 'cyan' | 'green' | 'amber' | 'red' | 'slate'; desc: string }> = {
  resident: { badge: 'cyan', desc: '每轮必发的高频基础工具' },
  perceive: { badge: 'green', desc: '只读取信息，不改变世界' },
  act: { badge: 'amber', desc: '会改变世界：写文件 / 执行 / 进程 / 出网' },
  govern: { badge: 'red', desc: '会改变云枢自身能力集 ⇒ 审批边界' },
}
const RISK_STYLE: Record<string, 'green' | 'amber' | 'red' | 'slate'> = {
  low: 'green', medium: 'amber', high: 'red', critical: 'red',
}
const EFFECT_LABEL: Record<string, string> = {
  read: '只读', write: '写入', execute: '执行', extend: '改自身',
}

type Merged = ToolItem & MetaRow

export default function ToolsToolset() {
  const [tools, setTools] = useState<ToolItem[]>([])
  const [meta, setMeta] = useState<Record<string, MetaRow>>({})
  const [planes, setPlanes] = useState<PlaneDef[]>([])
  const [undeclared, setUndeclared] = useState<string[]>([])
  const [metaOk, setMetaOk] = useState(true)
  /** 三档标识的图例文案与计数（后端 callability_note / callability_marks；缺失即空） */
  const [callabilityNote, setCallabilityNote] = useState('')
  const [callabilityMarks, setCallabilityMarks] = useState<Record<string, number>>({})
  const [filter, setFilter] = useState<string>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    // 旧口径（启用状态）——失败则整体报错
    hubGet('/api/tools/config').then((r) => {
      setTools(pickList<ToolItem>(r, 'tools'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })

    // 治理元数据——独立降级：拿不到就不显示平面，不影响启停功能
    hubGet('/api/agent-lines/planes').then((r) => {
      const resp = r as {
        tools?: MetaRow[]
        planes?: PlaneDef[]
        tools_without_declaration?: string[]
        callability_marks?: Record<string, number>
        callability_note?: string
      }
      const map: Record<string, MetaRow> = {}
      for (const row of resp.tools ?? []) {
        if (row?.name) map[row.name] = row
      }
      setMeta(map)
      setPlanes(resp.planes ?? [])
      setUndeclared(resp.tools_without_declaration ?? [])
      setCallabilityNote(resp.callability_note ?? '')
      setCallabilityMarks(resp.callability_marks ?? {})
      setMetaOk(true)
    }).catch(() => {
      // 拿不到治理元数据 ⇒ 连带清掉图例（不留上一次的陈旧标注）
      setMetaOk(false)
      setCallabilityNote('')
      setCallabilityMarks({})
    })
  }

  useEffect(load, [])

  const toggle = async (name: string, enabled: boolean) => {
    try {
      await hubPost('/api/tools/toggle', { name, enabled: !enabled })
      load()
    } catch (e) { setError(String(e)) }
  }

  const merged: Merged[] = useMemo(
    () => tools.map((t) => ({ ...t, ...(meta[t.name] ?? {}) })),
    [tools, meta],
  )

  /** 按平面分组；未登记 plane 的工具单列一组（fail-closed 语义下它们不会进装配） */
  const groups = useMemo(() => {
    const byPlane = new Map<string, Merged[]>()
    for (const t of merged) {
      const p = (t.plane && PLANE_ORDER.includes(t.plane as typeof PLANE_ORDER[number])) ? t.plane : 'undeclared'
      if (!byPlane.has(p)) byPlane.set(p, [])
      byPlane.get(p)!.push(t)
    }
    const order = [...PLANE_ORDER, 'undeclared']
    return order
      .filter((p) => byPlane.has(p))
      .map((p) => ({ plane: p, rows: byPlane.get(p)!.sort((a, b) => a.name.localeCompare(b.name)) }))
  }, [merged])

  const shown = filter === 'all' ? groups : groups.filter((g) => g.plane === filter)

  /**
   * 图例：后端给了说明文案（新接口）或至少一个工具带标识时才显示。
   * 旧后端（两个字段都没有）⇒ 整行不渲染，页面与本改动之前完全一致。
   */
  const marksText = callabilityCounts(callabilityMarks)
  const anyMark = useMemo(
    () => merged.some((t) => hasCallabilityMark(t.callability)),
    [merged],
  )
  const showLegend = metaOk && (callabilityNote !== '' || marksText !== '' || anyMark)

  const columns = [
    {
      key: 'name', title: '工具', render: (r: Merged) => (
        <div>
          <div className="flex flex-wrap items-center gap-1.5 font-medium text-slate-200">
            <span>{String(r.name)}</span>
            {/* 可调用性标识：mark 缺失时该组件不渲染任何东西 */}
            <CallabilityBadge info={r.callability} name={String(r.name)} />
            {r.needs_approval && (
              <span className="inline-flex items-center gap-1 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-300">
                <ShieldAlert size={10} /> 需审批
              </span>
            )}
            {r.internal && (
              <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400">内部</span>
            )}
          </div>
          {r.description && <div className="max-w-lg text-xs text-slate-500">{String(r.description)}</div>}
        </div>
      ),
    },
    {
      key: 'effect', title: '效果', render: (r: Merged) =>
        r.effect ? <Badge color="slate">{EFFECT_LABEL[r.effect] ?? String(r.effect)}</Badge> : '-',
    },
    {
      key: 'risk', title: '风险', render: (r: Merged) =>
        r.risk ? <Badge color={RISK_STYLE[r.risk] ?? 'slate'}>{String(r.risk)}</Badge> : '-',
    },
    {
      key: 'category', title: '分类', render: (r: Merged) =>
        r.category ? <Badge color="cyan">{String(r.category)}</Badge> : '-',
    },
    {
      key: 'enabled', title: '状态', render: (r: Merged) =>
        <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge>,
    },
    {
      key: 'actions', title: '操作', render: (r: Merged) => (
        <button
          onClick={() => toggle(String(r.name), Boolean(r.enabled))}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs ${r.enabled ? 'bg-slate-800 text-slate-300 hover:bg-slate-700' : 'bg-emerald-600 text-white hover:bg-emerald-500'}`}
        >
          <Power size={12} /> {r.enabled ? '停用' : '启用'}
        </button>
      ),
    },
  ]

  return (
    <div className="p-6">
      <PageHeader
        title="工具集"
        description="按四能力平面分组的工具总览：常驻 / 感知 / 行动 / 治理（治理平面 = 审批边界）"
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}

      {!metaOk && (
        <div className="mb-4 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-300">
          未取到治理元数据（/api/agent-lines/planes 不可用），当前仅显示旧口径的分类与启停状态。
        </div>
      )}

      {undeclared.length > 0 && (
        <div className="mb-4 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
          以下工具缺少 plane/effect/risk 声明，主线装配会 fail-closed 拒绝它们：
          {undeclared.join('、')}
        </div>
      )}

      {/* 平面筛选 */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button
          onClick={() => setFilter('all')}
          className={`rounded-md px-3 py-1.5 text-xs ${filter === 'all' ? 'bg-slate-700 text-slate-100' : 'bg-slate-800/60 text-slate-400 hover:bg-slate-700/60'}`}
        >
          全部 {merged.length}
        </button>
        {groups.map((g) => {
          const def = planes.find((p) => p.key === g.plane)
          const style = PLANE_STYLE[g.plane]
          return (
            <button
              key={g.plane}
              onClick={() => setFilter(g.plane)}
              title={def?.hint ?? style?.desc ?? ''}
              className={`rounded-md px-3 py-1.5 text-xs ${filter === g.plane ? 'bg-slate-700 text-slate-100' : 'bg-slate-800/60 text-slate-400 hover:bg-slate-700/60'}`}
            >
              {def?.label ?? (g.plane === 'undeclared' ? '未声明' : g.plane)} {g.rows.length}
            </button>
          )
        })}
      </div>

      {/* 可调用性图例：三档标识均为后端判定结果的直接映射，此处只做说明 */}
      {showLegend && (
        <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
          <span className="text-slate-400">可调用性标识</span>
          <span>{callabilityLegend(callabilityNote)}</span>
          {marksText && <span className="ml-auto text-slate-500">{marksText}</span>}
        </div>
      )}

      {loading ? <Loading /> : (
        <div className="space-y-5">
          {shown.map((g) => {
            const def = planes.find((p) => p.key === g.plane)
            const style = PLANE_STYLE[g.plane]
            return (
              <Card key={g.plane}>
                <div className="mb-3 flex items-center gap-2 px-1">
                  {style
                    ? <Badge color={style.badge}>{def?.label ?? g.plane}</Badge>
                    : <Badge color="slate">未声明平面</Badge>}
                  <span className="text-xs text-slate-500">
                    {def?.hint ?? style?.desc ?? '无治理声明 —— 主线装配会拒绝这些工具'}
                  </span>
                  <span className="ml-auto text-xs text-slate-500">{g.rows.length} 个</span>
                </div>
                <DataTable data={g.rows} keyField="name" columns={columns} />
              </Card>
            )
          })}
          {shown.length === 0 && (
            <Card><div className="p-4 text-sm text-slate-400">该平面下暂无工具。</div></Card>
          )}
        </div>
      )}
    </div>
  )
}
