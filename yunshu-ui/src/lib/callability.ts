/**
 * 可调用性标注（工具 / 技能「可被 LLM 调用」统一字段）——类型 + 展示层 + 只读客户端
 * ------------------------------------------------------------------
 * 契约来源：
 *   - `agent/lines/callability.py`（字段定义与判定规则，唯一口径）
 *   - `agent/server_routes/routes_agent_lines.py`
 *       · `GET /api/agent-lines/planes`  —— 每行工具附带 `callability`（可能为 `{}`）
 *       · `GET /api/capability-manifest` —— 工具 + 技能同构清单（`manifest.entries`）
 *
 * 【设计取舍】
 *   - 复用既有 `lib/apiClient.request`（fetch + 自动附带 Bearer），不新建 HTTP 层；
 *   - 判定的**权威在后端**：本模块只做"取字段 + 拼提示文案"，绝不重算 ✅/⚠️/❌，
 *     否则迟早出现第二份口径；
 *   - `mark` 缺失 / `callability` 为 `{}`（旧后端或清单不可用）⇒ 全部展示函数返回空，
 *     界面退化为"没有徽章"，**不报错、不占位**。
 */
import { request } from './apiClient'

// ═══════════════════════════════════════════════════════════
//  三档标识（文案与后端 MARK_* 常量同源）
// ═══════════════════════════════════════════════════════════

export const MARK_CALLABLE = '✅ 可调用'
export const MARK_CONDITIONAL = '⚠️ 条件可调用'
export const MARK_BLOCKED = '❌ 不可调用'

/** 图例里的固定展示顺序：可调用 → 条件可调用 → 不可调用 */
export const MARK_ORDER: readonly string[] = [MARK_CALLABLE, MARK_CONDITIONAL, MARK_BLOCKED]

/** 后端 `callability_note` 取不到时的兜底图例文案（语义与后端一致） */
export const CALLABILITY_LEGEND_FALLBACK =
  '✅ 可被模型发起 / ⚠️ 可执行但触发有条件（需人工确认，或由系统·人工触发，如技能） / ❌ 不可达（无执行器·已停用·被策略拒绝）'

// ═══════════════════════════════════════════════════════════
//  类型（与后端 entries 条目字段对齐；工具与技能同构）
// ═══════════════════════════════════════════════════════════

/** 一条能力的可调用性标注（`callability` 字段 / 清单条目共用） */
export interface CallabilityInfo {
  /** 能力名（工具名 / 技能 id）——两个清单共用同一主键名 */
  tool_name?: string
  tool_type?: string
  /**
   * 来源口径：`repo` = 提交清单里的仓库可复现条目；`runtime` = 只在运行时目录/台账里
   * 才有的条目（REST 端点请求期补算，见 `CapabilityManifestResponse.runtime_skills`）。
   * 界面按同一套判定显示标识，只在图例里提示运行时条目数。
   */
  scope?: string
  /** 生效值：是否允许 LLM 发起调用 */
  llm_callable?: boolean
  /** auto（模型自主）/ required（必须调用）/ manual（仅人工/系统） */
  callable_mode?: string
  schema_registered?: boolean
  /** 宿主执行器 ID 或 `模块:函数` */
  host_executor?: string
  permission_level?: string
  sandbox_allowed?: boolean
  /** llm_callable=false 时非空，说明缺什么 */
  reason?: string
  /** ✅/⚠️/❌ 三档展示标识 */
  mark?: string
  /** 是否**可达**（有执行器、有内容实体、未停用、权限未拒、非内部专用）——❌ 的唯一判据 */
  reachable?: boolean
  /** 真实触发者：model（模型发起）/ system（系统·宿主）/ human（人工）/ none（不可达） */
  trigger?: string
  /** 标识成因类别，决定措辞：unreachable / not_model_initiated / needs_approval / '' */
  reason_kind?: string
  /** 判否的逐条理由（硬阻断：不可达） */
  blockers?: string[]
  /** 可达但"不由模型发起"的逐条理由（软阻断：manual / 声明 false / 缺参数契约） */
  soft_blockers?: string[]
  /** soft_blockers 的机器可读代码 */
  soft_codes?: string[]
  /** ⚠️ 的条件说明 */
  conditions?: string[]
  /** 正交轴的补充说明（如 sandbox_allowed=false） */
  notes?: string[]
  declared_in?: string
}

/** `GET /api/capability-manifest` 的清单统计 */
export interface CapabilityManifestCounts {
  total?: number
  callable?: number
  conditional?: number
  blocked?: number
  by_type?: Record<string, number>
  by_permission_level?: Record<string, number>
  blocked_reasons?: Record<string, number>
}

/** 清单条目（工具 + 技能同构，技能侧另有 `skill_*` 诊断字段） */
export interface CapabilityManifestEntry extends CallabilityInfo {
  tool_name: string
  skill_status?: string
  has_scripts?: boolean
}

export interface CapabilityManifest {
  schema_version?: number
  generated_at?: string
  field_spec?: string[]
  vocabulary?: { mark?: string[] }
  rule?: string
  counts?: CapabilityManifestCounts
  tools?: CapabilityManifestEntry[]
  /** 技能子集（`tool_type == "skill"`） */
  skills?: CapabilityManifestEntry[]
  entries?: CapabilityManifestEntry[]
}

export interface CapabilityManifestResponse {
  ok: boolean
  manifest: CapabilityManifest
  /**
   * 只在运行时目录/台账里存在的技能标注（每条 `scope === 'runtime'`）。
   * 清单文件必须能从干净 checkout 复算，故这些条目不在文件里，由端点请求期补算；
   * 界面把它们并进同一张 id→标注 的表，否则那几行会没有徽章
   * （实测 id=`skill`（易之三义）就曾如此）。
   */
  runtime_skills?: CallabilityInfo[]
  runtime_note?: string
  path?: string
  updated_at?: number
}

// ═══════════════════════════════════════════════════════════
//  合并（仓库口径 + 运行时口径）
// ═══════════════════════════════════════════════════════════

/**
 * 把若干批标注合并成 `{能力名: 标注}`；**同名以后出现的为准**
 *
 * 用法：`indexCallability([...runtime, ...repo])` ⇒ 仓库口径覆盖运行时口径
 * （清单文件是权威，运行时补算只在文件没覆盖到的地方补位）。
 */
export function indexCallability(
  ...batches: (CallabilityInfo[] | undefined | null)[]
): Record<string, CallabilityInfo> {
  const out: Record<string, CallabilityInfo> = {}
  for (const batch of batches) {
    for (const e of batch ?? []) {
      if (e?.tool_name) out[e.tool_name] = e
    }
  }
  return out
}

// ═══════════════════════════════════════════════════════════
//  展示层（纯函数；入参缺失一律返回空，调用方据此不渲染）
// ═══════════════════════════════════════════════════════════

export type CallabilityBadgeColor = 'green' | 'amber' | 'red' | 'slate'

/** 取三档标识；`callability` 为 `{}` / `mark` 缺失 ⇒ 空串（退化为"无徽章"） */
export function callabilityMark(info?: CallabilityInfo | null): string {
  return typeof info?.mark === 'string' ? info.mark.trim() : ''
}

export function hasCallabilityMark(info?: CallabilityInfo | null): boolean {
  return callabilityMark(info) !== ''
}

/**
 * 标识 → 徽章配色（按标识**语义**匹配，不按全等：后端文案里带 U+FE0F 变体选择符，
 * 严格 `===` 容易因不可见字符差异静默失配）
 */
export function callabilityColor(mark?: string | null): CallabilityBadgeColor {
  const m = typeof mark === 'string' ? mark : ''
  if (m.includes('⚠')) return 'amber'
  if (m.includes('❌')) return 'red'
  if (m.includes('✅')) return 'green'
  return 'slate'
}

const MODE_LABEL: Record<string, string> = {
  auto: '模型自主', required: '必须调用', manual: '仅人工/系统',
}
const LEVEL_LABEL: Record<string, string> = {
  public: '公开', internal: '内部', restricted: '受限',
}
/** 触发者措辞（与后端 `trigger` 取值同源） */
const TRIGGER_LABEL: Record<string, string> = {
  model: '模型发起',
  system: '系统 / 宿主触发（非模型发起）',
  human: '人工触发',
  none: '无（不可达）',
}
/**
 * `reason` 的标签依赖成因类别 —— 这是本次语义修正的关键：
 * 技能这类"可达但不经模型发起"的条目，`reason` 说的是**触发方式**，不是"不可用"。
 */
const REASON_LABEL: Record<string, string> = {
  unreachable: '不可调用原因',
  not_model_initiated: '触发说明',
  needs_approval: '说明',
}

/**
 * 悬浮说明：标识 + 触发者 + 权限/调用模式 + reason + conditions + notes + 执行器 + 声明出处
 * 【不易】逐项判空后再 push，缺失的项**不留空行**（`title` 里的空行会显示成空白段）
 */
export function callabilityTitle(info?: CallabilityInfo | null, name?: string): string {
  if (!info) return ''
  const lines: string[] = []

  const mark = callabilityMark(info)
  if (mark) lines.push(name ? `${mark} · ${name}` : mark)

  if (info.trigger) {
    lines.push(`触发方式：${TRIGGER_LABEL[info.trigger] ?? info.trigger}`)
  }

  const meta: string[] = []
  if (info.permission_level) {
    meta.push(`权限 ${LEVEL_LABEL[info.permission_level] ?? info.permission_level}（${info.permission_level}）`)
  }
  if (info.callable_mode) {
    meta.push(`调用模式 ${MODE_LABEL[info.callable_mode] ?? info.callable_mode}`)
  }
  if (meta.length) lines.push(meta.join(' · '))

  if (info.reason) {
    const fallback = info.llm_callable === false ? '不可调用原因' : '说明'
    const label = REASON_LABEL[info.reason_kind ?? ''] ?? fallback
    lines.push(`${label}：${info.reason}`)
  }
  for (const it of info.conditions ?? []) if (it) lines.push(`条件：${it}`)
  for (const it of info.notes ?? []) if (it) lines.push(`说明：${it}`)
  if (info.host_executor) lines.push(`执行器：${info.host_executor}`)
  if (info.declared_in) lines.push(`声明：${info.declared_in}`)

  return lines.join('\n')
}

/** 图例文案：优先用后端 `callability_note`，缺失则用本地兜底常量 */
export function callabilityLegend(note?: string | null): string {
  const text = typeof note === 'string' ? note.trim() : ''
  return text || CALLABILITY_LEGEND_FALLBACK
}

/** `callability_marks` → 图例后缀（形如 `✅ 可调用 80 · ⚠️ 条件可调用 10`；顺序固定） */
export function callabilityCounts(marks?: Record<string, number> | null): string {
  if (!marks) return ''
  const keys = MARK_ORDER.filter((k) => (marks as Record<string, number>)[k])
  const rest = Object.keys(marks).filter((k) => !MARK_ORDER.includes(k) && marks[k])
  return [...keys, ...rest].map((k) => `${k} ${marks[k]}`).join(' · ')
}

// ═══════════════════════════════════════════════════════════
//  只读客户端
// ═══════════════════════════════════════════════════════════

/** 工具 / 技能统一可调用性清单（工具端点在 routes_agent_lines.py；404 = 清单未生成） */
export function fetchCapabilityManifest(): Promise<CapabilityManifestResponse> {
  return request<CapabilityManifestResponse>('/api/capability-manifest')
}
