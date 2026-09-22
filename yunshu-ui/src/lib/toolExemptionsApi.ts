/**
 * 工具豁免（「需确认」徽章背后的开关）API 客户端
 * ------------------------------------------------------------------
 * 契约来源：`/api/cp/tool-exemptions`（开关中心的工具级豁免视图）：
 *   GET  → { ok, exempt: string[], source: 'ui_override'|'env'|'default', items: [...] }
 *   POST → { ok, changed, exempt: string[], outcome }
 *          4xx → { ok: false, code, message }
 *
 * 【为什么不直接消费 `request` 抛出的 ApiError】
 *   `lib/apiClient.ts::request` 在 HTTP !ok 时把 `body.error` 当 message；本端点按
 *   开关中心口径回的是 `{ ok:false, code, message }`，直接把 ApiError 抛给 UI 会丢掉
 *   人话 message（只剩 "HTTP 403"），而本仓库硬纪律是**失败必须如实上屏**。
 *   所以这里只做一次归一：把 code + message 原样装进 ToolExemptionError，
 *   调用方（exemption.tsx）用 exemptionErrorText() 直接渲染 —— 不改动 apiClient 本身，
 *   避免影响其它上百个调用点。
 */

import { ApiError, request } from './apiClient'

const PREFIX = '/api/cp'

/** 生效来源（与开关中心同一套口径；未知取值原样透传，不猜） */
export type ExemptionSource = 'ui_override' | 'env' | 'default'

export interface ToolExemptionItem {
  tool: string
  /** 该工具的描述符等级（L1/L2/…），仅作展示 */
  level?: string
  effect?: string
  risk?: string
  /** 后端认定的当前是否已豁免 */
  exempt: boolean
  /** false = 这个开关放不了它（描述符硬要求），UI 必须锁住而不是假装能点 */
  exemptable: boolean
  /** exemptable=false 时的人话原因（直接进 title，用户才知道为什么点不动） */
  blocked_reason?: string
}

export interface ToolExemptionsView {
  ok: boolean
  exempt: string[]
  source?: ExemptionSource | string
  /** 开关中心解析器给的人话来源（后端 `resolve().source_label`）；优先于前端本地表 */
  source_label?: string
  /** 来源是环境变量且不可改 ⇒ 界面改也改不动（后端会回 locked_by_env） */
  env_locked?: boolean
  /** 后端对"能不能改"的判定（保留字段，UI 目前只用 env_locked 做提示） */
  editable?: boolean
  /** 该开关在登记表里的键名（排查用） */
  key?: string
  items: ToolExemptionItem[]
}

/**
 * 变更应答。后端 200 时回的是 `{ok:true, key, ...set_exempt(), ...view()}`：
 * 即**连同最新视图一起回**（items/exempt/source 都在里面），所以前端不必再补一次 GET。
 * `changed:false` 不是失败：目标工具已在名单里（重复写入被如实跳过），message 会说明原因。
 */
export interface ToolExemptionChange extends Omit<Partial<ToolExemptionsView>, 'ok'> {
  ok: boolean
  changed: boolean
  exempt: string[]
  outcome?: Record<string, unknown> | null
  /** changed=false 等"没改"场景的原因说明 */
  message?: string
}

/** 归一后的豁免接口错误：code 是后端判决（settings_denied / locked_by_env / …） */
export class ToolExemptionError extends Error {
  readonly code: string
  readonly status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ToolExemptionError'
    this.code = code
    this.status = status
  }
}

function normalize(e: unknown): ToolExemptionError {
  if (e instanceof ApiError) {
    const body = (e.details ?? {}) as Record<string, unknown>
    const human = typeof body.message === 'string' && body.message ? body.message : e.message
    return new ToolExemptionError(e.code || 'unknown_error', human, e.status)
  }
  if (e instanceof Error) return new ToolExemptionError('unknown_error', e.message, 0)
  return new ToolExemptionError('unknown_error', String(e), 0)
}

/** 读取豁免清单（含逐工具的 exemptable / blocked_reason） */
export function fetchToolExemptions(): Promise<ToolExemptionsView> {
  return request<ToolExemptionsView>(`${PREFIX}/tool-exemptions`).catch((e) => {
    throw normalize(e)
  })
}

/**
 * 放宽 / 收紧某工具的确认要求。
 *
 * 【reason 只在放宽时传】收紧（exempt=false）不需要解释自己为什么变严格；
 * 放宽才需要留下可追溯的理由（后端会记入审计）。
 */
export function setToolExemption(
  tool: string,
  exempt: boolean,
  reason?: string,
): Promise<ToolExemptionChange> {
  const body: Record<string, unknown> = { tool, exempt }
  const trimmed = (reason ?? '').trim()
  if (exempt && trimmed) body.reason = trimmed
  return request<ToolExemptionChange>(`${PREFIX}/tool-exemptions`, { method: 'POST', body }).catch((e) => {
    throw normalize(e)
  })
}

/** 上屏用文案：`code：message`（code 缺失时只给 message，绝不吞掉判决码） */
export function exemptionErrorText(e: unknown): string {
  if (e instanceof ToolExemptionError) {
    return e.code ? `${e.code}：${e.message}` : e.message
  }
  if (e instanceof Error) return e.message || String(e)
  return String(e)
}

/** 来源文案（取值未知时由调用方回落到原始字符串，不编造含义） */
export const EXEMPTION_SOURCE_LABELS: Record<string, string> = {
  ui_override: '开关中心覆盖层（界面设置）',
  env: '环境变量',
  default: '内置默认',
}
