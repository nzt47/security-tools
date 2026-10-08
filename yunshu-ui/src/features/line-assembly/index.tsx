/**
 * 主线 × 四面 总览（2026-10-08）
 * ------------------------------------------------------------------
 * 一条主线档案在四个地方被**共用**：
 *   ① 工具面 —— `POST /api/agent-lines/preview` 的 `preview`（`assemble()` 的可解释 trace）
 *   ② 技能面 —— `GET /api/agent-lines/<id>` 的 `skills`（`SkillPack` 判定）
 *   ③ 提示词面 —— 同端点的 `prompt_fragments`（role=line 片段）
 *   ④ 分身面 —— 同端点的 `subagent_assembly`（`resolve_subagent_assembly`，与派发路径同源）
 *
 * 「主线管理」页逐条编辑时四面都能看，但**看不了横向对比**：哪条线在派给分身时被收紧了、
 * 哪条线根本没有提示词片段、哪条线的技能白名单里有写错的 id。本页就是那张横向表。
 *
 * 【为什么逐条取详情，而不是让前端自己算四面】四段的判定全在后端（`assemble` /
 * `resolve_skill_pack` / `line_fragment_for_profile` / `resolve_subagent_assembly`）；
 * 前端再算一遍就是第二份口径 —— 本页只做"批量取回 + 如实上屏"。
 * 【零清单进导航】本文件是 `src/features/<key>/index.tsx`（`export default` + `manifest`），
 * 由 `workbench/featureRegistry.ts` 构建期自动发现并挂上导航，不改任何清单。
 * 【旧后端容错】某一段字段缺失 ⇒ 该格显示「—（后端未返回）」，**不假装是 0**
 * —— "没有这一面"和"这一面是空的"是两回事。
 */
import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Layers, RefreshCw, Search } from 'lucide-react'
import {
  fetchLine,
  fetchLines,
  readSubagentAssembly,
  type LineDetailResponse,
  type LineProfile,
  type SubagentAssemblyInfo,
} from '@/lib/agentLinesApi'

export const manifest = { key: 'line-assembly', label: '主线 × 四面', icon: Layers, order: 920 }

/** 一行的取数状态（详情按 id 逐条取；失败如实记账，不静默留白） */
interface Row {
  line: LineProfile
  detail: LineDetailResponse | null
  error: string
}

const DASH = '—（后端未返回）'

function fragCount(detail: LineDetailResponse | null): number | null {
  const raw = detail?.prompt_fragments
  return Array.isArray(raw) ? raw.length : null
}

function fragSources(detail: LineDetailResponse | null): string[] {
  const raw = detail?.prompt_fragments
  if (!Array.isArray(raw)) return []
  return raw
    .map((f) => (f && typeof f === 'object' ? (f as Record<string, unknown>).source : ''))
    .filter((s): s is string => typeof s === 'string' && s !== '')
}

function Chip({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  const cls: Record<string, string> = {
    slate: 'border-slate-700 bg-slate-800/60 text-slate-300',
    cyan: 'border-cyan-800 bg-cyan-950/40 text-cyan-300',
    amber: 'border-amber-800 bg-amber-950/40 text-amber-300',
    red: 'border-red-900 bg-red-950/40 text-red-300',
    green: 'border-emerald-800 bg-emerald-950/40 text-emerald-300',
  }
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[11px] ${cls[tone] ?? cls.slate}`}>
      {children}
    </span>
  )
}

/** 分身面摘要（判定全部来自后端装配单；本函数只挑字段上屏） */
function subagentSummary(sa: SubagentAssemblyInfo | null): { text: string; tone: string } | null {
  if (!sa) return null
  if (!sa.available) {
    return { text: `装不上：${sa.reason || '（后端未给原因）'}`, tone: 'red' }
  }
  const mode = sa.mode === 'line' ? '沿主线' : (sa.mode === 'default-readonly' ? '只读默认集' : sa.mode)
  const approval = sa.needs_approval.length ? ` · 需确认 ${sa.needs_approval.length}` : ''
  return { text: `${mode} · ${sa.tools.length} 个工具${approval}`, tone: sa.mode === 'line' ? 'green' : 'slate' }
}

export default function LineAssemblyOverview() {
  const [rows, setRows] = useState<Row[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    fetchLines()
      .then(async (list) => {
        if (cancelled) return
        const lines = list.lines ?? []
        setActive(list.active ?? null)
        setRows(lines.map((line) => ({ line, detail: null, error: '' })))
        setLoading(false)
        // 逐条取详情：每条的四面判定都在后端；失败的那条如实记账，不影响其余行
        await Promise.all(lines.map(async (line) => {
          try {
            const detail = await fetchLine(line.id)
            if (!cancelled) {
              setRows((prev) => prev.map((r) => (r.line.id === line.id ? { ...r, detail } : r)))
            }
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e)
            if (!cancelled) {
              setRows((prev) => prev.map((r) => (r.line.id === line.id ? { ...r, error: msg } : r)))
            }
          }
        }))
      })
      .catch((e) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => load(), [load])

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return rows
    return rows.filter((r) =>
      r.line.id.toLowerCase().includes(needle) || (r.line.name ?? '').toLowerCase().includes(needle))
  }, [rows, q])

  return (
    <div className="space-y-4 p-6" data-testid="line-assembly-overview">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
            <Layers size={18} className="text-cyan-400" /> 主线 × 四面
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            一条主线档案在「工具 / 技能 / 提示词 / 分身」四个面上的样子（横向对比）。
            四段判定全部来自后端，本页只上屏；逐条编辑与实时预览仍在「工具调用 → 主线管理」。
          </p>
        </div>
        <button
          onClick={() => load()}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
        >
          <RefreshCw size={12} /> 重新读取
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-2 text-sm text-red-300">
          读取主线列表失败：{error}
        </div>
      )}
      {loading && <div className="text-sm text-slate-500">加载中…</div>}

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="flex items-center gap-1 rounded-lg border border-slate-700 px-2 py-1">
          <Search size={12} className="text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="按 id / 名称过滤"
            className="w-48 bg-transparent text-xs text-slate-200 outline-none"
          />
        </span>
        <span className="text-slate-500">
          共 {rows.length} 条主线{active ? `；当前激活：${active}` : '；当前未装线（全量工具）'}
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-900/60 text-slate-400">
            <tr>
              <th className="px-3 py-2">主线</th>
              <th className="px-3 py-2">① 工具面</th>
              <th className="px-3 py-2">② 技能面</th>
              <th className="px-3 py-2">③ 提示词面</th>
              <th className="px-3 py-2">④ 分身面</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const d = r.detail
              const sa = readSubagentAssembly(d)
              const sub = subagentSummary(sa.present ? sa.assembly : null)
              const skills = d?.skills
              const frags = fragCount(d)
              const isOpen = open === r.line.id
              return (
                <Fragment key={r.line.id}>
                  <tr
                    data-testid={`line-row-${r.line.id}`}
                    onClick={() => setOpen(isOpen ? null : r.line.id)}
                    className="cursor-pointer border-t border-slate-800/60 hover:bg-slate-800/30"
                  >
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-slate-200">{r.line.id}</span>
                        <span className="text-slate-500">{r.line.name}</span>
                        {active === r.line.id && <Chip tone="cyan">已激活</Chip>}
                        {!r.line.enabled && <Chip tone="amber">已停用</Chip>}
                      </div>
                    </td>
                    <td className="px-3 py-2" data-testid={`face-tools-${r.line.id}`}>
                      {r.error ? <span className="text-red-300">读取失败：{r.error}</span>
                        : d?.preview ? (
                          <span className="text-slate-300">
                            {d.preview.count}
                            <span className="text-slate-500"> / 上限 {d.preview.max_tools}</span>
                            {(d.preview.needs_approval?.length ?? 0) > 0 && (
                              <span className="ml-1 text-amber-300">需确认 {d.preview.needs_approval.length}</span>
                            )}
                            {d.preview.over_budget && <span className="ml-1 text-amber-300">保底超限</span>}
                          </span>
                        ) : <span className="text-slate-600">{loading ? '…' : DASH}</span>}
                    </td>
                    <td className="px-3 py-2" data-testid={`face-skills-${r.line.id}`}>
                      {skills ? (
                        <span className="text-slate-300">
                          {skills.unrestricted
                            ? <Chip>不限制</Chip>
                            : <Chip tone="cyan">白名单 {skills.allowed?.length ?? 0}</Chip>}
                          {(skills.unknown?.length ?? 0) > 0 && (
                            <span className="ml-1 text-red-300">写错 {skills.unknown.length}</span>
                          )}
                        </span>
                      ) : <span className="text-slate-600">{loading ? '…' : DASH}</span>}
                    </td>
                    <td className="px-3 py-2" data-testid={`face-prompt-${r.line.id}`}>
                      {frags === null
                        ? <span className="text-slate-600">{loading ? '…' : DASH}</span>
                        : (frags > 0
                          ? <span className="text-slate-300">{frags} 段 <span className="text-slate-500">{fragSources(d).join(' ')}</span></span>
                          : <span className="text-slate-500">不注入（{d?.prompt_fragments_note ? '见原因' : '无片段'}）</span>)}
                    </td>
                    <td className="px-3 py-2" data-testid={`face-subagent-${r.line.id}`}>
                      {sub
                        ? <span className={sub.tone === 'red' ? 'text-red-300' : 'text-slate-300'}>{sub.text}</span>
                        : <span className="text-slate-600">{loading ? '…' : DASH}</span>}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-t border-slate-800/60 bg-slate-950/40">
                      <td className="px-3 py-3" colSpan={5} data-testid={`line-detail-${r.line.id}`}>
                        <div className="grid gap-3 md:grid-cols-2">
                          <div>
                            <div className="mb-1 text-[11px] text-slate-500">① 工具面（主智能体装配；后端 trace 原文）</div>
                            <div className="flex flex-wrap gap-1">
                              {(d?.preview.tools ?? []).map((t) => <Chip key={t}>{t}</Chip>)}
                              {!(d?.preview.tools ?? []).length && <span className="text-[11px] text-slate-600">{DASH}</span>}
                            </div>
                          </div>
                          <div>
                            <div className="mb-1 text-[11px] text-slate-500">② 技能面（本线允许注入的技能）</div>
                            <div className="flex flex-wrap gap-1">
                              {skills?.unrestricted
                                ? <span className="text-[11px] text-slate-500">不限制（本线未限定技能范围）</span>
                                : (skills?.allowed ?? []).map((s) => <Chip key={s} tone="cyan">{s}</Chip>)}
                              {(skills?.unknown ?? []).map((s) => <Chip key={s} tone="red">{s}（查不到）</Chip>)}
                              {!skills && <span className="text-[11px] text-slate-600">{DASH}</span>}
                            </div>
                          </div>
                          <div>
                            <div className="mb-1 text-[11px] text-slate-500">③ 提示词面（role=line 片段）</div>
                            <div className="text-[11px] text-slate-400">
                              {frags === null
                                ? DASH
                                : (frags > 0
                                  ? `${frags} 段：${fragSources(d).join('、')}`
                                  : (d?.prompt_fragments_note || '本线不注入任何提示词片段'))}
                            </div>
                          </div>
                          <div>
                            <div className="mb-1 text-[11px] text-slate-500">④ 分身面（派一个分身时它拿到什么）</div>
                            {!sa.present
                              ? <div className="text-[11px] text-slate-600">{DASH}</div>
                              : sa.assembly!.available ? (
                                <>
                                  <div className="flex flex-wrap gap-1">
                                    {sa.assembly!.tools.map((t) => <Chip key={t} tone="green">{t}</Chip>)}
                                    {!sa.assembly!.tools.length && <span className="text-[11px] text-amber-300">一个都不给</span>}
                                  </div>
                                  <div className="mt-1 text-[11px] text-slate-500">{sa.assembly!.note}</div>
                                </>
                              ) : (
                                <div className="text-[11px] text-red-300">{sa.assembly!.reason}</div>
                              )}
                            {sa.present && sa.assembly!.semantics_note && (
                              <div className="mt-1 text-[11px] text-slate-500">{sa.assembly!.semantics_note}</div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
        {!visible.length && !loading && (
          <div className="px-3 py-4 text-xs text-slate-500">没有匹配的主线。</div>
        )}
      </div>

      <div className="text-[11px] text-slate-500">
        判定出处：工具面 <span className="font-mono">agent/lines/assembler.py</span> ·
        技能面 <span className="font-mono">agent/lines/skillpack.py</span> ·
        提示词面 <span className="font-mono">agent/orchestrator/prompt_builder.py</span> ·
        分身面 <span className="font-mono">agent/subagent/assembly.py</span>（与派发路径 <span className="font-mono">agent/tools/fan_out_tools.py</span> 同源）。
        本页不重算任何一项。
      </div>
    </div>
  )
}
