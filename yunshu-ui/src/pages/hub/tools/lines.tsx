/**
 * 主线管理 —— 能力平面档案（查看 / 新建 / 编辑 / 激活 / 删除）+ 装配预览
 * ------------------------------------------------------------------
 * 数据源：`/api/agent-lines*`（后端见 `agent/server_routes/routes_agent_lines.py`）
 *   - 档案落盘在 `data/agent_lines/<id>.yaml`，工具能力元数据在
 *     `data/tool_definitions/*.yaml`（唯一权威，本页只读不写）；
 *   - **预览由后端算**：`POST /api/agent-lines/preview` 直接回传 `assemble()` 的
 *     trace（保底入选 / 打分入选 / 被效果上限拒绝 / 被 mute / 被截断），
 *     本页不做二次计算 —— 否则会出现第二份装配口径。
 *   - `/planes` 每行工具还带 `callability`（「可被 LLM 调用」统一标注）：
 *     工具选择器直接显示三档标识（✅/⚠️/❌），悬浮给出 reason/条件/执行器。
 *     `callability` 为 `{}` 时徽章整体不渲染，选择器与加标注之前完全一致。
 *   - **预览同样带标注**：`tools_meta` 的每一行含 `callability`（后端与 `/planes`
 *     读同一份派生清单 `data/capability_manifest.json`，见 `_callability_index`），
 *     故预览面板的 chip 也显示标识（压缩为单字形，完整说明在 `title` 上）。
 *     响应里的 `callability_source` 标明该标注的出处，便于排查"标注不一致"。
 *
 * 【为什么左列表 + 右编辑器 + 常驻预览】
 *     权重这种东西"填数字看不出后果"：把 `plane_floors` 从 2 调到 0，
 *     表面上只是少一个数字，实际是"低权重平面可能被吃光名额"。
 *     所以预览必须与输入框同屏，且随输入防抖重算（改权重即时看效果）。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Check, Copy, Info, Layers, MessageSquare, Plus, Save, Search, ShieldAlert, Sparkles,
  Target, Trash2,
} from 'lucide-react'
import { Badge, CallabilityBadge, Card, ErrorBox, Loading, PageHeader } from '../components/ui'
import { callabilityCounts, callabilityLegend, hasCallabilityMark } from '@/lib/callability'
import {
  createLine,
  deleteLine,
  fetchLines,
  fetchPlanes,
  previewLine,
  saveLine,
  setActiveLine,
  validateLine,
  type AssemblyPreview,
  type LineProfile,
  type PlanesResponse,
  type SkillPackInfo,
  type ToolCatalogEntry,
} from '@/lib/agentLinesApi'
import {
  ExemptionStatusBar,
  ExemptionToggle,
  useToolExemptions,
  type ToolExemptionState,
} from './exemption'

// ═══════════════════════════════════════════════════════════
//  文案与常量（中文名与后端 PLANE_LABELS / EFFECT_LABELS 同源；
//  后端 /planes 已回传权威文案，此处只作"目录未加载时"的兜底）
// ═══════════════════════════════════════════════════════════

const PLANE_ORDER = ['resident', 'perceive', 'act', 'govern'] as const
const PLANE_LABELS: Record<string, string> = {
  resident: '常驻', perceive: '感知', act: '行动', govern: '治理',
}
const EFFECT_ORDER = ['read', 'write', 'execute', 'extend'] as const
const EFFECT_LABELS: Record<string, string> = {
  read: '只读', write: '写入', execute: '执行', extend: '扩展能力',
}
const RISK_LABELS: Record<string, string> = {
  low: '低', medium: '中', high: '高', critical: '严重',
}

type BadgeColor = 'green' | 'red' | 'amber' | 'cyan' | 'slate'

const RISK_BADGE: Record<string, BadgeColor> = {
  low: 'slate', medium: 'cyan', high: 'amber', critical: 'red',
}

const CHIP_CLASS: Record<string, string> = {
  slate: 'border-slate-700 bg-slate-800/60 text-slate-300',
  cyan: 'border-cyan-800 bg-cyan-950/40 text-cyan-300',
  amber: 'border-amber-800 bg-amber-950/40 text-amber-300',
  red: 'border-red-900 bg-red-950/40 text-red-300',
}

// ═══════════════════════════════════════════════════════════
//  纯函数工具
// ═══════════════════════════════════════════════════════════

function errText(e: unknown): string {
  if (e instanceof Error) return e.message || String(e)
  return String(e)
}

function num(v: unknown, fallback = 0): number {
  // 数字输入框被清空时 e.target.value 是 ''，Number('') === 0 会悄悄把值变成 0，
  // 这里把"空"当作"未填"回落到默认值（避免清空输入框就地把 max_tools 写成 0）
  if (v === '' || v === null || v === undefined) return fallback
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function textToList(text: string): string[] {
  return Array.from(new Set(
    text.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean),
  ))
}

function listToText(list?: string[]): string {
  return (list ?? []).join(', ')
}

/** 深拷贝一份档案（编辑不污染列表里的原对象） */
function cloneProfile(p: LineProfile): LineProfile {
  return {
    ...p,
    id: p.id || '',
    name: p.name || p.id || '',
    description: p.description || '',
    plane_weights: { ...(p.plane_weights ?? {}) },
    plane_floors: { ...(p.plane_floors ?? {}) },
    boost: [...(p.boost ?? [])],
    mute: [...(p.mute ?? [])],
    tags: [...(p.tags ?? [])],
    max_tools: num(p.max_tools, 20),
    effect_allow: [...(p.effect_allow ?? [])],
    requires_approval: [...(p.requires_approval ?? [])],
    skills: [...(p.skills ?? [])],
    prompt_note: p.prompt_note || '',
  }
}

/** 新建时的空档案（默认值来自后端 `defaults`，与 LineProfile 的 dataclass 默认一致） */
function blankProfile(defaults?: Partial<LineProfile> | null): LineProfile {
  const d = defaults ?? {}
  return {
    id: '',
    name: '',
    description: '',
    enabled: d.enabled ?? true,
    plane_weights: { resident: 1, perceive: 1, act: 1, ...(d.plane_weights ?? {}) },
    plane_floors: { resident: 2, perceive: 2, act: 2, ...(d.plane_floors ?? {}) },
    boost: [],
    mute: [],
    tags: [],
    max_tools: num(d.max_tools, 20),
    effect_allow: d.effect_allow ? [...d.effect_allow] : ['read', 'write', 'execute'],
    requires_approval: [],
    allow_govern: d.allow_govern ?? false,
    skills: [],
    prompt_note: '',
  }
}

/** 规范化串（用于"是否已保存"判定：字段顺序无关、集合顺序无关） */
function canonical(p: LineProfile): string {
  const pairs = (o?: Record<string, number>) =>
    Object.keys(o ?? {}).sort().map((k) => [k, num((o ?? {})[k])])
  return JSON.stringify({
    id: p.id, name: p.name, description: p.description, enabled: !!p.enabled,
    plane_weights: pairs(p.plane_weights), plane_floors: pairs(p.plane_floors),
    boost: [...(p.boost ?? [])].sort(), mute: [...(p.mute ?? [])].sort(),
    tags: [...(p.tags ?? [])].sort(), max_tools: num(p.max_tools),
    effect_allow: [...(p.effect_allow ?? [])].sort(),
    requires_approval: [...(p.requires_approval ?? [])].sort(),
    allow_govern: !!p.allow_govern,
    skills: [...(p.skills ?? [])].sort(), prompt_note: p.prompt_note || '',
  })
}

// ═══════════════════════════════════════════════════════════
//  提示词片段（后端算，前端只呈现）
// ═══════════════════════════════════════════════════════════

/**
 * 本线会注入系统提示词的片段。
 *
 * 数据来自 `POST /api/agent-lines/preview|validate` 的 `prompt_fragments`
 * （判定实现在 `agent/orchestrator/prompt_builder.py::line_fragment_for_profile`，
 * 与运行时装配共用同一个函数）。
 *
 * 【为什么前端一个字都不算】再判一遍就是第二份"什么会被注入提示词"的口径，
 * 与系统提示词的真实组装结果迟早分叉 —— 与本页技能区块同一条纪律
 * （见 `agentLinesApi.SkillPackInfo` 的注释）。
 */
export type PromptFragmentInfo = {
  /** 片段拥有者角色（当前只有 'line'，见 agent/prompt_manager/roles.py::PROMPT_ROLES） */
  role: string
  /** 审计来源，形如 line:engineering */
  source: string
  /** 片段原文（后端已 strip） */
  content: string
  chars: number
  priority: number
  /** true = 预算超限时可裁剪；role=line 为 false（硬片段） */
  croppable: boolean
}

/**
 * 读后端给的片段字段（**只做形状规范化，不做任何判定**）。
 *
 * `available=false` 表示后端根本没返回该字段（旧后端 / 未部署）⇒ 面板整块不渲染，
 * 行为与本页接上片段之前一致；形状不对的条目直接忽略，绝不因此让预览崩掉。
 */
export function readPromptFragments(resp: unknown): {
  available: boolean
  fragments: PromptFragmentInfo[]
  note: string
} {
  const src = (resp ?? {}) as { prompt_fragments?: unknown; prompt_fragments_note?: unknown }
  const note = typeof src.prompt_fragments_note === 'string' ? src.prompt_fragments_note : ''
  if (!Array.isArray(src.prompt_fragments)) {
    return { available: false, fragments: [], note }
  }
  const fragments: PromptFragmentInfo[] = []
  for (const it of src.prompt_fragments) {
    if (!it || typeof it !== 'object') continue
    const o = it as Record<string, unknown>
    if (typeof o.role !== 'string' || typeof o.content !== 'string') continue
    fragments.push({
      role: o.role,
      source: typeof o.source === 'string' ? o.source : '',
      content: o.content,
      chars: num(o.chars, o.content.length),
      priority: num(o.priority),
      croppable: o.croppable === true,
    })
  }
  return { available: true, fragments, note }
}

function planeSummary(p: LineProfile): string {
  const parts = PLANE_ORDER
    .filter((k) => num(p.plane_weights?.[k]) > 0 || num(p.plane_floors?.[k]) > 0)
    .map((k) => `${PLANE_LABELS[k]} ${num(p.plane_weights?.[k])}`)
  return parts.length ? parts.join(' · ') : '无启用平面（装配结果为空）'
}


// ═══════════════════════════════════════════════════════════
//  小组件
// ═══════════════════════════════════════════════════════════

function Field({ label, hint, children }: {
  label: string; hint?: string; children: ReactNode
}) {
  return (
    <label className="block">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-xs text-slate-300">{label}</span>
        {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
      </div>
      {children}
    </label>
  )
}

const INPUT_CLASS =
  'w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-700'

function Toggle({ on, label, hint, onChange }: {
  on: boolean; label: string; hint?: string; onChange: (v: boolean) => void
}) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${
        on ? 'border-emerald-700 bg-emerald-950/40 text-emerald-300'
          : 'border-slate-700 bg-slate-900 text-slate-400 hover:bg-slate-800'
      }`}
    >
      <span className={`flex h-3.5 w-3.5 items-center justify-center rounded border ${
        on ? 'border-emerald-500' : 'border-slate-600'
      }`}>
        {on && <Check size={10} />}
      </span>
      <span>{label}</span>
      {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
    </button>
  )
}

function ToolChips({ names, meta, reasons, color = 'slate' }: {
  names: string[]
  meta?: Record<string, ToolCatalogEntry>
  reasons?: Record<string, string>
  color?: 'slate' | 'cyan' | 'amber' | 'red'
}) {
  if (!names.length) return <span className="text-[11px] text-slate-500">无</span>
  return (
    <div className="flex flex-wrap gap-1">
      {names.map((n) => (
        <span
          key={n}
          title={reasons?.[n] || meta?.[n]?.description || n}
          className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${CHIP_CLASS[color]}`}
        >
          <span className="font-mono">{n}</span>
          {meta?.[n] && (
            <span className="text-slate-500">{PLANE_LABELS[meta[n].plane] ?? meta[n].plane}</span>
          )}
          {/* 可调用性标识（压缩为单字形，完整说明在 title 上）：'tools_meta' 由后端
              与目录端点同源回传（data/capability_manifest.json） */}
          <CallabilityBadge info={meta?.[n]?.callability} name={n} compact />
        </span>
      ))}
    </div>
  )
}

/** 可搜索的工具多选（boost / mute 共用；两者只是语义不同，交互同款） */
function ToolPicker({ title, hint, tone, tools, selected, exemptions, onToggle, onToggleExemption }: {
  title: string
  hint: string
  tone: 'cyan' | 'red'
  tools: ToolCatalogEntry[]
  selected: string[]
  /** 工具豁免（「需确认」开关）的全局状态：徽章真值与锁定原因都取自此 */
  exemptions: ToolExemptionState
  onToggle: (name: string) => void
  onToggleExemption: (name: string, next: boolean) => void
}) {
  const [q, setQ] = useState('')
  const chosen = useMemo(() => new Set(selected), [selected])
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return tools
    return tools.filter((t) => (
      t.name.toLowerCase().includes(needle)
      || (t.category ?? '').toLowerCase().includes(needle)
      || (t.plane ?? '').toLowerCase().includes(needle)
      || (t.tags ?? []).some((tag) => tag.toLowerCase().includes(needle))
    ))
  }, [tools, q])

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium text-slate-300">{title}</span>
          <span className="text-[11px] text-slate-500">{hint}</span>
        </div>
        <span className={`font-mono text-[11px] ${tone === 'cyan' ? 'text-cyan-400' : 'text-red-400'}`}>
          已选 {selected.length}
        </span>
      </div>
      <div className="flex items-center gap-2 border-b border-slate-800/60 px-3 py-2">
        <Search size={12} className="shrink-0 text-slate-500" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索工具名 / 分类 / 平面 / 标签"
          className="w-full bg-transparent text-xs text-slate-200 placeholder-slate-600 outline-none"
        />
        {q && (
          <button onClick={() => setQ('')} className="shrink-0 text-[11px] text-slate-500 hover:text-slate-300">
            清空
          </button>
        )}
      </div>
      <div className="max-h-56 overflow-y-auto p-1.5">
        {tools.length === 0 && (
          <div className="px-2 py-3 text-xs text-slate-500">工具目录未加载，暂不能选择工具</div>
        )}
        {tools.length > 0 && rows.length === 0 && (
          <div className="px-2 py-3 text-xs text-slate-500">没有匹配的工具</div>
        )}
        {rows.map((t) => {
          const on = chosen.has(t.name)
          return (
            // 【结构】行 = 「选中工具的大按钮」+「豁免开关」并列，而不是把开关塞进按钮里：
            //   HTML 不允许按钮嵌套按钮；开关又必须是真按钮（可键盘触发、可禁用、可挂原因 title）。
            //   底色/文字色因此上移到这层容器（原来挂在按钮上）。
            <div
              key={t.name}
              className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-xs ${
                on
                  ? (tone === 'cyan' ? 'bg-cyan-950/40 text-cyan-200' : 'bg-red-950/40 text-red-200')
                  : 'text-slate-300 hover:bg-slate-800/60'
              }`}
            >
              <button
                onClick={() => onToggle(t.name)}
                title={t.description || t.name}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <span className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border ${
                  on ? 'border-current' : 'border-slate-600'
                }`}>
                  {on && <Check size={10} />}
                </span>
                <span className="w-40 shrink-0 truncate font-mono">{t.name}</span>
                <Badge color="slate">{PLANE_LABELS[t.plane] ?? t.plane}</Badge>
                <Badge color="cyan">{EFFECT_LABELS[t.effect] ?? t.effect}</Badge>
                <Badge color={RISK_BADGE[t.risk] ?? 'slate'}>
                  险 {RISK_LABELS[t.risk] ?? t.risk}
                </Badge>
                {/* 可调用性标识：mark 缺失时不渲染（退化为现状） */}
                <CallabilityBadge info={t.callability} name={t.name} />
                <span className="truncate text-[11px] text-slate-500">{t.category}</span>
              </button>
              {/* 「需确认」徽章 = 豁免开关（豁免清单没读到时它自己退回静态徽章） */}
              {t.needs_approval && (
                <ExemptionToggle
                  name={t.name}
                  item={exemptions.lookup(t.name)}
                  exempt={exemptions.isExempt(t.name)}
                  pending={exemptions.pendingTool === t.name}
                  disabled={exemptions.pendingTool !== ''}
                  onToggle={onToggleExemption}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** 可折叠的清单块（被拒 / 被截断这类"辅助解释"默认收起，避免淹没主结论） */
function FoldList({ title, names, meta, reasons, color, defaultOpen = false }: {
  title: string
  names: string[]
  meta?: Record<string, ToolCatalogEntry>
  reasons?: Record<string, string>
  color?: 'slate' | 'cyan' | 'amber' | 'red'
  defaultOpen?: boolean
}) {
  if (!names.length) return null
  return (
    <details open={defaultOpen} className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
      <summary className="cursor-pointer text-xs text-slate-300">
        {title}（{names.length}）
      </summary>
      <div className="mt-2">
        <ToolChips names={names} meta={meta} reasons={reasons} color={color} />
      </div>
    </details>
  )
}

/**
 * 技能包区块（装配预览内）—— 只呈现**后端判定结果**，前端不重算
 * ------------------------------------------------------------------
 * 数据来自 `POST /api/agent-lines/preview` 的 `skills`（`agent/lines/skillpack.py`
 * 的 `SkillPack.to_dict()`）。为什么前端一个字都不算：算第二遍就等于出现第二份
 * 「本线允许哪些技能」的口径，两份迟早分叉（本仓的「十三处工具真相」都是这么长出来的）。
 *
 * 三档呈现（与后端 mode/source 一一对应）：
 *   - `unrestricted` ⇒ 「不限制」：本线没限定技能范围（未装线 / skills 为空都是这一档）；
 *   - `whitelist`    ⇒ 列出 allowed；declared-but-missing 单独用红色列出（unknown）；
 *   - 字段缺失（旧后端）⇒ 整块不渲染，行为与本页接上技能判定之前完全一致。
 */
export function SkillPackBlock({ skills }: { skills?: SkillPackInfo | null }) {
  if (!skills) return null
  const allowed = skills.allowed ?? []
  const unknown = skills.unknown ?? []
  const requested = skills.requested ?? []
  return (
    <div
      className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
      data-skill-pack={skills.mode}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-300">
        <Sparkles size={12} className="text-cyan-400" />
        <span>技能</span>
        <Badge color={skills.unrestricted ? 'slate' : 'cyan'}>
          {skills.unrestricted ? '不限制' : `白名单（声明 ${requested.length}）`}
        </Badge>
        <span className="text-[11px] text-slate-500">{skills.source_label}</span>
      </div>

      {skills.unrestricted ? (
        <div className="text-[11px] text-slate-500" data-skill-unrestricted="1">
          本线未限定技能范围：全局启用的技能都可注入。
          （未装线、或本线 `skills` 为空，都是这一档 —— 空 = 未表态，不等于「一个都不给」。）
        </div>
      ) : (
        <div className="space-y-2">
          {allowed.length > 0 ? (
            <div data-skill-allowed={String(allowed.length)}>
              <div className="mb-1 text-[11px] text-slate-500">本线允许注入的技能</div>
              <ToolChips names={allowed} color="cyan" />
            </div>
          ) : (
            <div className="text-[11px] text-amber-300" data-skill-allowed="0">
              本线声明的技能在技能目录里一条都查不到 ⇒ 本轮不注入任何技能段。
            </div>
          )}
          {unknown.length > 0 && (
            <div data-skill-unknown={String(unknown.length)}>
              <div className="mb-1 text-[11px] text-red-300">
                声明了但技能目录里查不到（{unknown.length}）：既无实体、也无声明，保存时会被校验拦下
              </div>
              <ToolChips names={unknown} color="red" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  装配预览面板
// ═══════════════════════════════════════════════════════════

function PreviewPanel({ preview, skills, promptFragments, promptFragmentsNote,
                       promptInjectionState, issues, notes, error, pending }: {
  preview: AssemblyPreview | null
  /** 本线技能包判定（后端算）；旧后端不返回 ⇒ 技能区块整体不渲染 */
  skills?: SkillPackInfo | null
  /** 本线会注入的提示词片段（后端算）；null = 后端没返回该字段 ⇒ 整块不渲染 */
  promptFragments?: PromptFragmentInfo[] | null
  /** 后端给的人读说明（片段为空时为什么为空） */
  promptFragmentsNote?: string
  /** 这些片段本轮是否真的会进提示词：未激活 / 已激活但未保存 / 已生效 */
  promptInjectionState: 'not_active' | 'unsaved' | 'injected'
  issues: string[]
  notes: string[]
  error: string
  pending: boolean
}) {
  const meta = preview?.tools_meta
  const reasons = preview?.reasons
  return (
    <div className="space-y-3">
      {error && <ErrorBox message={error} />}
      {pending && <div className="text-[11px] text-cyan-400">正在重算装配…</div>}

      {/* ── 本线会注入的提示词片段（role=line）───────────────────────────
          prompt_note 此前是**死字段**（UI 写着"随本线注入"，运行时无人消费）。
          接到系统提示词后，这里是它唯一的可视化口径，数据全部来自后端
          （`/api/agent-lines/preview` 的 `prompt_fragments` /
          `prompt_fragments_note`），前端不重算判定。三种状态如实呈现：
            ① 后端给了片段 ⇒ 列出 role/source/priority/原文（role=line 不可裁剪）；
            ② 后端给了空列表 ⇒ 显示后端给的原因（字段为空 / 本线停用）；
            ③ 后端没这个字段（旧后端）⇒ 整块不渲染，与本页接上它之前一致。
          后端权威实现：agent/orchestrator/prompt_builder.py::line_fragment_for_profile。 */}
      {promptFragments && (
        <div data-testid="line-prompt-fragment"
          className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-300">
            <MessageSquare size={12} className="text-cyan-400" />
            <span>本线会注入的提示词片段</span>
            <span className="font-mono text-[11px] text-slate-500">role=line</span>
            {promptFragments.length > 0 && (
              <Badge color="cyan">{promptFragments.length} 段</Badge>
            )}
          </div>
          {promptFragments.length > 0 ? (
            <div className="space-y-2">
              {promptFragments.map((f) => (
                <div key={f.source + ':' + f.role} data-testid="line-fragment-item">
                  <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
                    <span className="font-mono text-cyan-300" data-testid="line-fragment-source">
                      {f.source}
                    </span>
                    <span className="font-mono">role={f.role}</span>
                    <span>priority={f.priority}</span>
                    <span>{f.croppable ? '可裁剪' : '不可裁剪（硬片段）'}</span>
                    <span>{f.chars} 字符</span>
                    <span data-testid="line-fragment-state"
                      className={promptInjectionState === 'injected' ? 'text-emerald-400' : 'text-amber-400'}>
                      {promptInjectionState === 'injected'
                        ? '本线已激活且已保存：本轮对话会注入'
                        : (promptInjectionState === 'unsaved'
                            ? '本线已激活，但当前改动未保存：保存后才会注入'
                            : '本线未激活：激活并保存后才会注入')}
                    </span>
                  </div>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[11px] text-slate-300">{f.content}</pre>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[11px] text-slate-500" data-testid="line-fragment-empty">
              {promptFragmentsNote || '本线不注入任何提示词片段。'}
            </div>
          )}
        </div>
      )}

      {preview && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-slate-300">
              工具数
              <span className="ml-1.5 font-mono text-lg text-white">{preview.count}</span>
              <span className="mx-1 text-slate-500">/</span>
              <span className="font-mono text-slate-400">上限 {preview.max_tools}</span>
            </span>
            <Badge color={preview.over_budget ? 'amber' : 'green'}>
              {preview.over_budget ? '保底超出上限' : '在名额内'}
            </Badge>
            {preview.needs_approval.length > 0 && (
              <Badge color="amber">需人工确认 {preview.needs_approval.length}</Badge>
            )}
            {(preview.denied_by_effect.length + preview.denied_unknown.length) > 0 && (
              <Badge color="slate">
                被拒 {preview.denied_by_effect.length + preview.denied_unknown.length}
              </Badge>
            )}
          </div>

          {preview.over_budget && (
            <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-300">
              已启用平面的保底名额整体优先于打分名额：宁可略微超出上限，也不让某个平面清零。
              想让总数回到上限内，请下调各平面 plane_floors 或提高 max_tools。
            </div>
          )}

          {preview.needs_approval.length > 0 && (
            <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2">
              <div className="mb-1.5 flex items-center gap-1.5 text-xs text-amber-300">
                <ShieldAlert size={12} />
                需要人工确认（治理平面 / 改变能力集 / 高危）
              </div>
              <ToolChips names={preview.needs_approval} meta={meta} reasons={reasons} color="amber" />
            </div>
          )}

          {issues.length > 0 && (
            <div className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2">
              <div className="mb-1 text-xs text-red-300">档案校验问题（{issues.length}）</div>
              <ul className="list-inside list-disc space-y-0.5 text-[11px] text-red-300">
                {issues.map((it) => <li key={it}>{it}</li>)}
              </ul>
            </div>
          )}

          {notes.length > 0 && (
            <div className="rounded-lg border border-cyan-900/60 bg-cyan-950/30 px-3 py-2">
              <div className="mb-1 text-xs text-cyan-300">系统自动规范化</div>
              <ul className="list-inside list-disc space-y-0.5 text-[11px] text-cyan-300">
                {notes.map((it) => <li key={it}>{it}</li>)}
              </ul>
            </div>
          )}

          <div className="space-y-2">
            {Object.keys(preview.by_plane).length === 0 && (
              <div className="text-xs text-slate-500">
                没有任何平面被启用（plane_weights 全为 0），本线装配结果为空。
              </div>
            )}
            {Object.entries(preview.by_plane).map(([plane, names]) => (
              <div key={plane} className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
                <div className="mb-1.5 flex items-center gap-2 text-xs text-slate-300">
                  <Layers size={12} className="text-cyan-400" />
                  <span>{PLANE_LABELS[plane] ?? plane}</span>
                  <span className="font-mono text-[11px] text-slate-500">{plane}</span>
                  <span className="ml-auto font-mono text-[11px] text-slate-400">{names.length} 个</span>
                </div>
                <ToolChips names={names} meta={meta} reasons={reasons} color="cyan" />
              </div>
            ))}
          </div>

          {/* 技能段：与工具段同一份「后端判定」，前端只上屏 */}
          <SkillPackBlock skills={skills} />

          <div className="space-y-2">
            <FoldList title="被效果上限 / 平面未启用拦下" names={preview.denied_by_effect}
              meta={meta} color="slate" />
            <FoldList title="无 plane/effect 声明（fail-closed 拒绝）" names={preview.denied_unknown}
              meta={meta} color="red" />
            <FoldList title="被 mute 显式排除" names={preview.muted} meta={meta} color="slate" />
            <FoldList title="名额不足被截断" names={preview.truncated} meta={meta} color="amber" />
            {preview.denied_unknown.length > 0 && (
              <div className="text-[11px] text-red-300">
                未登记的工具一律不给（无法证明其安全边界）：请先在
                data/tool_definitions/*.yaml 里补 plane/effect/risk。
              </div>
            )}
          </div>

          <div className="text-[11px] text-slate-500">
            鼠标悬停工具名可看到入选理由（平面保底 / 打分入选）。
          </div>
        </>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  页面
// ═══════════════════════════════════════════════════════════

export default function ToolsAgentLines() {
  const [catalog, setCatalog] = useState<PlanesResponse | null>(null)
  const [lines, setLines] = useState<LineProfile[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [broken, setBroken] = useState<string[]>([])
  const [defaults, setDefaults] = useState<Partial<LineProfile> | null>(null)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [draft, setDraft] = useState<LineProfile | null>(null)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const [preview, setPreview] = useState<AssemblyPreview | null>(null)
  /** 技能包判定（后端 /preview 回传）：本线允许注入哪些技能、有没有写错的 id */
  const [previewSkills, setPreviewSkills] = useState<SkillPackInfo | null>(null)
  /** 本线会注入系统提示词的片段（后端 /preview 回传；null = 后端没给该字段 ⇒ 不渲染） */
  const [previewPromptFragments, setPreviewPromptFragments] =
    useState<PromptFragmentInfo[] | null>(null)
  /** 片段为空时后端给的人读原因 */
  const [previewPromptFragmentsNote, setPreviewPromptFragmentsNote] = useState('')
  const [previewIssues, setPreviewIssues] = useState<string[]>([])
  const [previewNotes, setPreviewNotes] = useState<string[]>([])
  const [previewError, setPreviewError] = useState('')
  const [previewing, setPreviewing] = useState(false)
  /** 豁免变更后强制重算预览：预览里的「需人工确认 N」必须跟着豁免走，否则那张卡会说谎 */
  const [previewNonce, setPreviewNonce] = useState(0)

  /** 工具豁免（「需确认」徽章背后的开关）；它自己管状态，读不到也不拖垮本页 */
  const exemptions = useToolExemptions()

  const tools = catalog?.tools ?? []
  /** 图例：后端给了 callability_note / 计数，或至少一个工具带标识时才显示（旧后端 ⇒ 不显示） */
  const callabilityText = callabilityCounts(catalog?.callability_marks)
  const showCallabilityLegend = tools.some((t) => hasCallabilityMark(t.callability))
    || Boolean(catalog?.callability_note) || callabilityText !== ''

  /** 载入分类法 + 档案列表；`select` 决定是否动当前选中项 */
  const load = async (opts: { select?: string | null | 'auto'; silent?: boolean } = {}) => {
    const { select, silent } = opts
    if (!silent) setLoading(true)
    let planesErr = ''
    let listErr = ''
    const [planes, list] = await Promise.all([
      fetchPlanes().catch((e) => { planesErr = errText(e); return null }),
      fetchLines().catch((e) => { listErr = errText(e); return null }),
    ])
    if (planes) {
      setCatalog(planes)
      setDefaults((prev) => prev ?? (planes.defaults ?? null))
    }
    if (list) {
      setLines(list.lines ?? [])
      setActive(list.active ?? null)
      setBroken(list.broken ?? [])
      if (list.defaults) setDefaults(list.defaults)
    }
    setError([planesErr, listErr].filter(Boolean).join('；'))
    if (list && select !== undefined) {
      const rows = list.lines ?? []
      if (select === null) {
        setSelectedId(null); setIsNew(false); setDraft(null)
      } else {
        const pick = select === 'auto'
          ? (rows.find((l) => l.id === list.active) ?? rows[0] ?? null)
          : (rows.find((l) => l.id === select) ?? null)
        if (pick) {
          setSelectedId(pick.id); setIsNew(false); setDraft(cloneProfile(pick))
        } else if (select !== 'auto') {
          setSelectedId(null); setIsNew(false); setDraft(null)
        }
      }
    }
    if (!silent) setLoading(false)
  }

  useEffect(() => { void load({ select: 'auto' }) }, [])

  // ── 预览：随 draft 防抖重算（"改权重即时看效果"的动力来源） ──
  useEffect(() => {
    if (!draft) {
      setPreview(null); setPreviewSkills(null); setPreviewIssues([]); setPreviewNotes([])
      setPreviewPromptFragments(null); setPreviewPromptFragmentsNote('')
      setPreviewError(''); setPreviewing(false)
      return
    }
    if (!(draft.id || '').trim()) {
      setPreview(null); setPreviewSkills(null)
      setPreviewPromptFragments(null); setPreviewPromptFragmentsNote('')
      setPreviewIssues([]); setPreviewNotes([]); setPreviewing(false)
      setPreviewError('填写「主线 id」后自动计算装配预览')
      return
    }
    const controller = new AbortController()
    const timer = setTimeout(() => {
      setPreviewing(true)
      previewLine(draft, controller.signal)
        .then((r) => {
          setPreview(r.preview)
          // 旧后端没有 skills 字段 ⇒ null ⇒ 技能区块不渲染（退化为现状）
          setPreviewSkills(r.skills ?? null)
          // 旧后端没有 prompt_fragments 字段 ⇒ available=false ⇒ 整块不渲染（退化为现状）
          const pf = readPromptFragments(r)
          setPreviewPromptFragments(pf.available ? pf.fragments : null)
          setPreviewPromptFragmentsNote(pf.note)
          setPreviewIssues(r.issues ?? [])
          setPreviewNotes(r.notes ?? [])
          setPreviewError('')
        })
        .catch((e) => {
          if (controller.signal.aborted) return
          setPreviewError(errText(e))
        })
        .finally(() => {
          if (!controller.signal.aborted) setPreviewing(false)
        })
    }, 350)
    return () => { clearTimeout(timer); controller.abort() }
  }, [draft, previewNonce])

  // ── 编辑动作 ──

  const patch = (p: Partial<LineProfile>) =>
    setDraft((d) => (d ? { ...d, ...p } : d))

  const setWeight = (plane: string, value: number) =>
    setDraft((d) => (d ? { ...d, plane_weights: { ...d.plane_weights, [plane]: value } } : d))

  const setFloor = (plane: string, value: number) =>
    setDraft((d) => (d ? { ...d, plane_floors: { ...d.plane_floors, [plane]: value } } : d))

  const toggleIn = (
    key: 'boost' | 'mute' | 'effect_allow' | 'requires_approval' | 'tags' | 'skills',
    value: string,
  ) => setDraft((d) => {
    if (!d) return d
    const cur = d[key] ?? []
    return { ...d, [key]: cur.includes(value) ? cur.filter((x) => x !== value) : [...cur, value] }
  })

  const selectLine = (line: LineProfile) => {
    setSelectedId(line.id); setIsNew(false); setDraft(cloneProfile(line))
    setNotice('')
  }

  const startNew = () => {
    setSelectedId(null); setIsNew(true); setDraft(blankProfile(defaults)); setNotice('')
  }

  const startCopy = () => {
    if (!draft) return
    const base = cloneProfile(draft)
    setSelectedId(null)
    setIsNew(true)
    setDraft({
      ...base,
      id: `${base.id || 'line'}_copy`,
      name: `${base.name || base.id} 副本`,
    })
    setNotice('已复制为新档案：改好 id 后点「保存」')
  }

  const doSave = async () => {
    if (!draft) return
    const id = (draft.id || '').trim()
    if (!id) { setError('请先填写主线 id（小写字母开头，含小写字母/数字/_/-）'); return }
    setBusy(true)
    try {
      if (isNew) await createLine(draft)
      else await saveLine(id, draft)
      setNotice(isNew ? `已新建主线「${id}」` : `已保存主线「${id}」`)
      setError('')
      await load({ select: id, silent: true })
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async () => {
    if (!draft || isNew) { setError('该档案尚未保存，无需删除'); return }
    const id = draft.id
    const label = draft.name || id
    if (!window.confirm(`确认删除主线「${label}」？\n删除后其 YAML 档案会被移除，不可撤销；若它是当前激活主线，激活指针会一并清空。`)) return
    setBusy(true)
    try {
      await deleteLine(id)
      setNotice(`已删除主线「${id}」`)
      setError('')
      await load({ select: 'auto', silent: true })
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const doActivate = async (id: string | null) => {
    setBusy(true)
    try {
      await setActiveLine(id)
      setNotice(id ? `已切换到主线「${id}」` : '已回到「不装线」：本轮暴露全量工具')
      setError('')
      await load({ silent: true })
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  // ── 工具豁免：放宽先问理由，收紧不问（放宽才是需要留痕的方向） ──
  const doToggleExemption = async (tool: string, next: boolean) => {
    let reason = ''
    if (next) {
      // window.prompt 的默认值当占位用：用户直接回车＝不写理由（后端 reason 可选），
      // 取消（null）＝放弃本次提交，绝不静默替用户点下去
      const input = window.prompt(
        `放宽「${tool}」的人工确认要求\n\n这会全局生效（不止当前主线），并记入审计。\n请填写放宽原因：`,
        '（可选）例：受控环境内的批量迁移，已另行审批',
      )
      if (input === null) return
      reason = input.trim()
    }
    await exemptions.setExemption(tool, next, reason)
    // 切换后重算一次预览：预览里的 needs_approval 清单来自后端装配结果，
    // 不重算就会与刚到手的豁免真值互相矛盾
    setPreviewNonce((n) => n + 1)
  }

  const doValidate = async () => {
    if (!draft) return
    setBusy(true)
    try {
      const r = await validateLine(draft)
      setPreviewIssues(r.issues ?? [])
      setPreviewNotes(r.notes ?? [])
      setPreviewError('')
      setNotice(r.valid ? '校验通过（未保存）' : `校验未通过：${r.issues.length} 个问题`)
    } catch (e) {
      setPreviewError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  // ── 派生：是否与已保存内容不一致 ──
  const stored = useMemo(
    () => (draft ? lines.find((l) => l.id === draft.id) ?? null : null),
    [lines, draft],
  )
  const dirty = useMemo(() => {
    if (!draft) return false
    if (isNew || !stored) return true
    return canonical(stored) !== canonical(draft)
  }, [draft, stored, isNew])

  /**
   * 后端给的片段**本轮是否真的会进提示词**：片段判定由后端给，但"这条线有没有生效"
   * 是前端手里的两个事实（激活指针 + 草案是否已保存），故在页面这一层组合。
   */
  const promptInjectionState: 'not_active' | 'unsaved' | 'injected' =
    draft && active === draft.id ? (dirty ? 'unsaved' : 'injected') : 'not_active'

  if (loading) {
    return (
      <div className="p-6">
        <PageHeader title="主线管理" description="能力平面档案与装配预览" />
        <Loading />
      </div>
    )
  }

  return (
    <div className="p-6">
      <PageHeader
        title="主线管理"
        description="按四平面（常驻/感知/行动/治理）给不同 Agent 配一条能力主线；改权重可即时看到装配结果"
        actions={
          <div className="flex items-center gap-2">
            <button onClick={startNew}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500">
              <Plus size={12} /> 新建
            </button>
            <button onClick={startCopy} disabled={!draft || busy}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40">
              <Copy size={12} /> 复制
            </button>
            <button onClick={() => void doActivate(draft?.id ?? null)} disabled={!draft || isNew || busy}
              className="flex items-center gap-1.5 rounded-lg border border-emerald-800 px-3 py-1.5 text-xs text-emerald-300 hover:bg-emerald-950/50 disabled:opacity-40">
              <Target size={12} /> 设为当前
            </button>
            <button onClick={() => void doActivate(null)} disabled={busy}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800 disabled:opacity-40">
              不装线
            </button>
            <button onClick={() => void doDelete()} disabled={!draft || isNew || busy}
              className="flex items-center gap-1.5 rounded-lg border border-red-900/60 px-3 py-1.5 text-xs text-red-400 hover:bg-red-950 disabled:opacity-40">
              <Trash2 size={12} /> 删除
            </button>
          </div>
        }
      />

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {notice && (
        <div className="mb-4 rounded-lg border border-emerald-900/60 bg-emerald-950/30 px-4 py-2.5 text-sm text-emerald-300">
          {notice}
        </div>
      )}
      {/* 豁免接口是独立端点：它失败只影响开关，本页其余部分照常（故不并进上面的 error） */}
      <ExemptionStatusBar state={exemptions} onReload={() => void exemptions.reload()} />
      {broken.length > 0 && (
        <div className="mb-4 rounded-lg border border-amber-900/60 bg-amber-950/30 px-4 py-2.5 text-xs text-amber-300">
          以下档案文件读取失败（已在列表中略过）：{broken.join('、')}
        </div>
      )}
      {active === null && (
        <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-2.5 text-xs text-slate-400">
          当前<b className="text-slate-200">未装线</b>：运行时暴露全量工具。在左侧选一条主线后点「设为当前」即可启用。
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        {/* ── 左：主线列表 ── */}
        <div className="space-y-4">
          <Card title={`主线档案（${lines.length}）`}>
            {lines.length === 0 ? (
              <div className="py-6 text-center text-xs text-slate-500">
                暂无主线档案。点右上角「新建」创建第一条。
              </div>
            ) : (
              <div className="space-y-2">
                {lines.map((l) => {
                  const on = l.id === selectedId
                  return (
                    <button
                      key={l.id}
                      onClick={() => selectLine(l)}
                      className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                        on ? 'border-cyan-700 bg-cyan-950/30' : 'border-slate-800 bg-slate-900/40 hover:bg-slate-800/50'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-slate-200">
                          {l.name || l.id}
                        </span>
                        {active === l.id && <Badge color="green">当前</Badge>}
                      </div>
                      <div className="mt-1 truncate font-mono text-[11px] text-slate-500">{l.id}</div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        <Badge color="cyan">上限 {num(l.max_tools)}</Badge>
                        <Badge color={l.allow_govern ? 'amber' : 'slate'}>
                          治理{l.allow_govern ? '开' : '关'}
                        </Badge>
                        {!l.enabled && <Badge color="slate">停用</Badge>}
                        {active !== l.id && (
                          <span
                            onClick={(e) => { e.stopPropagation(); void doActivate(l.id) }}
                            className="ml-auto rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-400 hover:bg-slate-700 hover:text-slate-200"
                          >
                            设为当前
                          </span>
                        )}
                      </div>
                      <div className="mt-1.5 text-[11px] text-slate-500">{planeSummary(l)}</div>
                    </button>
                  )
                })}
              </div>
            )}
          </Card>

          {/* ── 说明：四平面 / effect 偏序 / 保底 ── */}
          <Card title="怎么理解这条线">
            <div className="space-y-2.5 text-[11px] leading-relaxed text-slate-400">
              <div>
                <div className="mb-1 flex items-center gap-1 text-slate-300">
                  <Layers size={12} className="text-cyan-400" /> 四平面
                </div>
                <ul className="space-y-0.5">
                  <li>· <b className="text-slate-300">常驻</b>：每轮必发（高频低 token）</li>
                  <li>· <b className="text-slate-300">感知</b>：只读取，不改变世界</li>
                  <li>· <b className="text-slate-300">行动</b>：改变世界</li>
                  <li>· <b className="text-slate-300">治理</b>：改变云枢自身能力集</li>
                </ul>
              </div>
              <div>
                <div className="mb-1 flex items-center gap-1 text-slate-300">
                  <ShieldAlert size={12} className="text-amber-400" /> effect 是偏序，不是集合
                </div>
                <p>
                  <span className="font-mono text-slate-300">read &lt; write &lt; execute &lt; extend</span>。
                  effect_allow 填的是<b className="text-slate-300">上限</b>：允许 execute
                  就等于同时允许 read 与 write；只有填了 extend 才允许改自身能力集。
                </p>
              </div>
              <div>
                <div className="mb-1 flex items-center gap-1 text-slate-300">
                  <ShieldAlert size={12} className="text-amber-400" /> 治理平面 = 审批边界
                </div>
                <p>
                  治理平面的工具会改动云枢自己的能力集，所以 plane=govern 的工具一律需要人工确认。
                  allow_govern 关闭时，govern 权重会被直接丢弃（不参与装配）；
                  打开时系统会自动把 extend 加入 effect_allow —— 否则治理平面会被效果上限
                  过滤成空集，「允许治理」就成了静默失效的开关。
                </p>
              </div>
              <div>
                <div className="mb-1 flex items-center gap-1 text-slate-300">
                  <Info size={12} className="text-cyan-400" /> 平面保底：防止"高优先级平面吃光名额"
                </div>
                <p>
                  装配顺序：①按 effect 上限过滤 → ②每个权重&gt;0 的平面先各取
                  plane_floors 个（保底）→ ③剩余名额按「平面权重 × 核心工具加成 ×
                  标签匹配」打分补足 → ④最后截到 max_tools，且
                  <b className="text-slate-300">保底名额整体优先于打分名额</b>。
                  所以把 act 权重调得很高也不会让常驻/感知清零；代价是名额紧张时
                  总数可能略微超出 max_tools（预览会标红提示）。
                </p>
              </div>
              <div>
                <div className="mb-1 flex items-center gap-1 text-slate-300">
                  <Info size={12} className="text-cyan-400" /> 其它
                </div>
                <p>
                  boost = 核心工具加成；mute = 明确排除；tags = 命中标签小幅加成；
                  <span className="font-mono">max_tools</span> = 单轮最多给模型看多少个工具。
                  激活指针写在 <span className="font-mono">data/agent_lines/_active.json</span>。
                </p>
              </div>
            </div>
          </Card>
        </div>

        {/* ── 右：编辑器 + 预览 ── */}
        <div className="space-y-4">
          {!draft ? (
            <Card title="档案编辑">
              <div className="py-8 text-center text-xs text-slate-500">
                从左侧选择一条主线，或点右上角「新建」开始配置。
              </div>
            </Card>
          ) : (
            <>
              <Card
                title={isNew ? '新建主线档案' : `编辑：${draft.name || draft.id}`}
                actions={
                  <div className="flex items-center gap-2">
                    {dirty && <Badge color="amber">未保存</Badge>}
                    {!isNew && <Badge color="cyan">上限 {num(draft.max_tools)}</Badge>}
                    <button onClick={() => void doValidate()} disabled={busy}
                      className="rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40">
                      校验
                    </button>
                    <button onClick={() => void doSave()} disabled={busy}
                      className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-500 disabled:opacity-40">
                      <Save size={12} /> 保存
                    </button>
                  </div>
                }
              >
                <div className="space-y-4">
                  {/* 基本信息 */}
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <Field label="主线 id" hint={isNew ? '小写字母开头，含小写字母/数字/_/-，2-41 字符' : '已保存的主线不可改名（改 id 请用新建 + 删除）'}>
                      <input
                        value={draft.id}
                        disabled={!isNew}
                        onChange={(e) => patch({ id: e.target.value })}
                        placeholder="例：engineering"
                        className={`${INPUT_CLASS} font-mono disabled:opacity-60`}
                      />
                    </Field>
                    <Field label="名称">
                      <input
                        value={draft.name}
                        onChange={(e) => patch({ name: e.target.value })}
                        placeholder="例：自主编码与工程交付"
                        className={INPUT_CLASS}
                      />
                    </Field>
                  </div>

                  <Field label="描述">
                    <input
                      value={draft.description}
                      onChange={(e) => patch({ description: e.target.value })}
                      placeholder="这条线是给谁用的、核心链路是什么"
                      className={INPUT_CLASS}
                    />
                  </Field>

                  <div className="flex flex-wrap items-center gap-2">
                    <Toggle on={!!draft.enabled} label="启用" onChange={(v) => patch({ enabled: v })} />
                    <Toggle
                      on={!!draft.allow_govern}
                      label="允许治理平面"
                      hint="打开后自动放行 extend"
                      onChange={(v) => patch({ allow_govern: v })}
                    />
                    <Field label="max_tools" hint="单轮最多暴露给模型的工具数">
                      <input
                        type="number" min={1} max={200}
                        value={num(draft.max_tools, 20)}
                        // 后端 `int(raw.get("max_tools") or 20)` 会把 0 当成"未填"回落 20，
                        // 故此处就地夹到 ≥1，避免输入框显示 0 而预览显示 20 的自相矛盾
                        onChange={(e) => patch({ max_tools: Math.max(1, num(e.target.value, 20)) })}
                        className="w-24 rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-200 outline-none"
                      />
                    </Field>
                  </div>

                  {/* 平面权重 + 保底 */}
                  <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
                    <div className="mb-3 flex items-center gap-2 text-xs text-slate-300">
                      <Layers size={12} className="text-cyan-400" />
                      平面权重与保底
                      <span className="text-[11px] text-slate-500">
                        权重 0 = 该平面不参与；保底 = 该平面至少先占几个名额
                      </span>
                    </div>
                    <div className="space-y-3">
                      {PLANE_ORDER.map((plane) => {
                        const w = num(draft.plane_weights?.[plane])
                        const floor = num(draft.plane_floors?.[plane])
                        const disabled = plane === 'govern' && !draft.allow_govern
                        return (
                          <div key={plane} className="flex items-center gap-3">
                            <span className={`w-16 shrink-0 text-xs ${disabled ? 'text-slate-600' : 'text-slate-300'}`}>
                              {PLANE_LABELS[plane]}
                            </span>
                            <input
                              type="range" min={0} max={3} step={0.1} value={w}
                              disabled={disabled}
                              onChange={(e) => setWeight(plane, num(e.target.value))}
                              className="flex-1 accent-cyan-500 disabled:opacity-40"
                            />
                            <input
                              type="number" min={0} max={3} step={0.1} value={w}
                              disabled={disabled}
                              onChange={(e) => setWeight(plane, num(e.target.value))}
                              className="w-20 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 outline-none disabled:opacity-40"
                            />
                            <span className="shrink-0 text-[11px] text-slate-500">保底</span>
                            <input
                              type="number" min={0} max={50} value={floor}
                              disabled={disabled}
                              onChange={(e) => setFloor(plane, num(e.target.value))}
                              className="w-20 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 outline-none disabled:opacity-40"
                            />
                          </div>
                        )
                      })}
                    </div>
                    {!draft.allow_govern && (
                      <div className="mt-2 text-[11px] text-slate-500">
                        治理平面权重会被丢弃：它改变云枢自身能力集，必须显式打开「允许治理平面」。
                      </div>
                    )}
                  </div>

                  {/* 治理策略 */}
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
                      <div className="mb-2 text-xs text-slate-300">effect_allow（效果上限）</div>
                      <div className="flex flex-wrap gap-2">
                        {EFFECT_ORDER.map((eff) => {
                          const on = (draft.effect_allow ?? []).includes(eff)
                          return (
                            <Toggle
                              key={eff}
                              on={on}
                              label={`${EFFECT_LABELS[eff]} ${eff}`}
                              onChange={() => toggleIn('effect_allow', eff)}
                            />
                          )
                        })}
                      </div>
                      <div className="mt-2 text-[11px] text-slate-500">
                        偏序上限：勾了 execute 就等于同时允许 read/write。
                      </div>
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
                      <div className="mb-2 text-xs text-slate-300">requires_approval（需人工确认）</div>
                      <div className="flex flex-wrap gap-2">
                        {EFFECT_ORDER.map((eff) => {
                          const on = (draft.requires_approval ?? []).includes(eff)
                          return (
                            <Toggle
                              key={eff}
                              on={on}
                              label={`${EFFECT_LABELS[eff]} ${eff}`}
                              onChange={() => toggleIn('requires_approval', eff)}
                            />
                          )
                        })}
                      </div>
                      <div className="mt-2 text-[11px] text-slate-500">
                        与工具自身的 risk 叠加：治理平面 / extend / critical 一律需要确认。
                      </div>
                    </div>
                  </div>

                  {/* boost / mute */}
                  {showCallabilityLegend && (
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
                      <span className="text-slate-400">可调用性标识</span>
                      <span>{callabilityLegend(catalog?.callability_note)}</span>
                      {callabilityText && <span className="ml-auto">{callabilityText}</span>}
                    </div>
                  )}
                  <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                    <ToolPicker
                      title="boost（核心工具加成）"
                      hint="跨平面 +60 分"
                      tone="cyan"
                      tools={tools}
                      selected={draft.boost ?? []}
                      exemptions={exemptions}
                      onToggle={(name) => toggleIn('boost', name)}
                      onToggleExemption={(name, next) => void doToggleExemption(name, next)}
                    />
                    <ToolPicker
                      title="mute（明确排除）"
                      hint="不进候选集"
                      tone="red"
                      tools={tools}
                      selected={draft.mute ?? []}
                      exemptions={exemptions}
                      onToggle={(name) => toggleIn('mute', name)}
                      onToggleExemption={(name, next) => void doToggleExemption(name, next)}
                    />
                  </div>

                  {/* 标签 / 技能 / 提示词 */}
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <Field label="tags" hint="命中标签的工具 +12 分（逗号分隔）">
                      <input
                        value={listToText(draft.tags)}
                        onChange={(e) => patch({ tags: textToList(e.target.value) })}
                        placeholder="例：code, orchestrate"
                        className={INPUT_CLASS}
                      />
                    </Field>
                    <Field label="skills" hint="本线允许注入的技能 id（逗号分隔）；留空 = 不限制">
                      <input
                        value={listToText(draft.skills)}
                        onChange={(e) => patch({ skills: textToList(e.target.value) })}
                        placeholder="例：engineering-test-delivery"
                        className={INPUT_CLASS}
                      />
                    </Field>
                  </div>

                  <Field label="prompt_note" hint="随本线注入的系统提示词片段（可留空）">
                    <textarea
                      value={draft.prompt_note}
                      onChange={(e) => patch({ prompt_note: e.target.value })}
                      rows={4}
                      placeholder="例：本线的交付标准是「改完并验证」……"
                      className={`${INPUT_CLASS} resize-y`}
                    />
                  </Field>
                </div>
              </Card>

              <Card
                title="装配预览（实时）"
                actions={
                  <span className="text-[11px] text-slate-500">
                    {catalog?.tool_source === 'declarations'
                      ? '候选集来自已声明工具（运行时注册表未加载）'
                      : `候选集来自运行时注册表（${catalog?.tool_count ?? 0} 个工具）`}
                  </span>
                }
              >
                <PreviewPanel
                  preview={preview}
                  skills={previewSkills}
                  promptFragments={previewPromptFragments}
                  promptFragmentsNote={previewPromptFragmentsNote}
                  promptInjectionState={promptInjectionState}
                  issues={previewIssues}
                  notes={previewNotes}
                  error={previewError}
                  pending={previewing}
                />
              </Card>

              {(catalog?.tools_without_declaration?.length ?? 0) > 0 && (
                <Card title="未登记的工具（fail-closed 拒绝）">
                  <div className="mb-2 text-[11px] text-slate-500">
                    以下工具在运行时存在，但没有 plane/effect 声明，装配时一律不给
                    （无法证明其安全边界）：请先在 data/tool_definitions/*.yaml 补齐。
                  </div>
                  <ToolChips names={catalog.tools_without_declaration} color="red" />
                </Card>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
