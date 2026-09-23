/**
 * 主线管理 API 客户端（能力平面 / 主线档案 / 装配预览）
 * ------------------------------------------------------------------
 * 契约来源：`agent/server_routes/routes_agent_lines.py`（前缀 `/api/agent-lines`）。
 *
 * 【设计取舍】
 *   - 复用既有 `lib/apiClient.request`（fetch + 自动附带 `Authorization: Bearer`，
 *     见 `lib/apiToken.ts`），**不新建 HTTP 层**；
 *   - 装配口径**只有一个**：预览一律由后端 `assemble()` 计算并原样回传
 *     （`AssemblyResult.to_dict()` 已含保底/打分/被拒理由的 trace），
 *     前端**不自行复算** —— 否则迟早出现第二份口径。
 */

import { request } from './apiClient'
import type { CallabilityInfo } from './callability'

const PREFIX = '/api/agent-lines'

// ═══════════════════════════════════════════════════════════
//  类型（与后端 LineProfile.to_dict / AssemblyResult.to_dict 对齐）
// ═══════════════════════════════════════════════════════════

export type PlaneKey = 'resident' | 'perceive' | 'act' | 'govern'
export type EffectKey = 'read' | 'write' | 'execute' | 'extend'
export type RiskKey = 'low' | 'medium' | 'high' | 'critical'

/** 一条主线档案（= `data/agent_lines/<id>.yaml`） */
export interface LineProfile {
  id: string
  name: string
  description: string
  enabled: boolean
  /** 平面权重：0 = 该平面不参与；越大越优先且召回越多 */
  plane_weights: Record<string, number>
  /** 每个平面的保底召回数（解决「高优先级平面吃光名额」的饥饿问题） */
  plane_floors: Record<string, number>
  /** 主线核心工具：权重加成（可跨平面） */
  boost: string[]
  /** 明确排除的工具 */
  mute: string[]
  /** 关注的标签：命中者获得小幅加成 */
  tags: string[]
  /** 单轮最多暴露给模型的工具数 */
  max_tools: number
  /** 效果上限（偏序 read < write < execute < extend） */
  effect_allow: string[]
  /** 需要人工确认的效果等级 */
  requires_approval: string[]
  /** 是否允许本主线调用 govern 平面（改变自身能力集） */
  allow_govern: boolean
  skills: string[]
  prompt_note: string
}

/** 工具目录条目（来自 data/tool_definitions/*.yaml，唯一权威） */
export interface ToolCatalogEntry {
  name: string
  category: string
  plane: PlaneKey
  effect: EffectKey
  risk: RiskKey
  tags: string[]
  needs_approval: boolean
  /** 内部工具：保留注册，但不进模型可见集 */
  internal: boolean
  description?: string
  /** 该工具「可被 LLM 调用」的统一标注；清单不可用时为 `{}`（见 lib/callability.ts） */
  callability?: CallabilityInfo
}

export interface LineListResponse {
  ok: boolean
  /** null = 不装线（全量工具） */
  active: string | null
  lines: LineProfile[]
  /** 读取失败的档案文件（如实回报，不静默吞掉） */
  broken: string[]
  defaults?: Partial<LineProfile>
  note?: string
}

export interface PlanesResponse {
  ok: boolean
  planes: { key: PlaneKey; label: string; hint: string; count: number }[]
  plane_counts: Record<string, number>
  effects: { key: EffectKey; label: string; rank: number }[]
  effect_order: EffectKey[]
  effect_note: string
  risks: { key: RiskKey; label: string; rank: number }[]
  tools: ToolCatalogEntry[]
  tool_count: number
  /** 运行时存在但没有 plane/effect 声明 ⇒ 装配时 fail-closed 拒绝（应为空） */
  tools_without_declaration: string[]
  /** 候选工具名来源：registry=运行时注册表；declarations=退回已声明集合 */
  tool_source: 'registry' | 'declarations'
  /** 三档标识的计数（形如 `{"⚠️ 条件可调用": 10, "✅ 可调用": 80}`；无标注时缺省/空表） */
  callability_marks?: Record<string, number>
  /** 三档标识的说明文案（图例直接用；取不到时前端有本地兜底） */
  callability_note?: string
  defaults?: Partial<LineProfile>
}

/** 本线的技能包判定（后端 `agent/lines/skillpack.py::SkillPack`；前端只呈现不重算） */
export interface SkillPackInfo {
  /** 生效主线 id；null = 未装线 */
  line_id: string | null
  /** 'unrestricted' | 'whitelist' */
  mode: string
  /** true = 本线对技能注入不设边界（未装线 / skills 为空） */
  unrestricted: boolean
  /** YAML 里声明的技能 id（去重保序） */
  requested: string[]
  /** 本线允许注入的技能 id（白名单模式下 = requested ∩ 运行时目录） */
  allowed: string[]
  /** 声明了但运行时目录里不存在的 id（如实回报，不静默吞掉） */
  unknown: string[]
  /** 判定来源（机器可读） */
  source: string
  /** 判定来源的人读文案（中文，单一来源在后端） */
  source_label: string
}

/** 一次装配的结果（含可解释 trace） */
export interface AssemblyPreview {
  line_id: string
  tools: string[]
  count: number
  by_plane: Record<string, string[]>
  denied_by_effect: string[]
  denied_unknown: string[]
  muted: string[]
  truncated: string[]
  reasons: Record<string, string>
  needs_approval: string[]
  max_tools: number
  over_budget: boolean
  tools_meta?: Record<string, ToolCatalogEntry>
  /** `tools_meta` 里 `callability` 的出处（派生清单路径，与 `/planes` 同源） */
  callability_source?: string
}

export interface PreviewResponse {
  ok: boolean
  line_id: string
  line: LineProfile
  preview: AssemblyPreview
  /** 技能包判定；旧后端不返回该字段（⇒ 面板不渲染技能区块） */
  skills?: SkillPackInfo
  issues: string[]
  /** 后端自动规范化说明（如 allow_govern 自动放行 extend） */
  notes: string[]
  tool_source: string
  saved: boolean
}

export interface ValidateResponse {
  ok: boolean
  valid: boolean
  issues: string[]
  line: LineProfile
  /** 技能包判定（与 /preview 同源同算） */
  skills?: SkillPackInfo
  notes: string[]
  saved: boolean
}

export interface LineDetailResponse {
  ok: boolean
  line: LineProfile
  preview: AssemblyPreview
  issues: string[]
  tool_source: string
}

// ═══════════════════════════════════════════════════════════
//  读
// ═══════════════════════════════════════════════════════════

/** 全部主线档案 + 当前激活指针 */
export function fetchLines(): Promise<LineListResponse> {
  return request<LineListResponse>(PREFIX)
}

/** 四平面/效果/风险分类法 + 全量工具目录（供选择器构建） */
export function fetchPlanes(): Promise<PlanesResponse> {
  return request<PlanesResponse>(`${PREFIX}/planes`)
}

/** 单条档案 + 现场装配预览 */
export function fetchLine(lineId: string): Promise<LineDetailResponse> {
  return request<LineDetailResponse>(`${PREFIX}/${encodeURIComponent(lineId)}`)
}

// ═══════════════════════════════════════════════════════════
//  预览 / 校验（零副作用：不落盘、不改激活指针）
// ═══════════════════════════════════════════════════════════

/** 按**未保存**的档案算装配结果（"改权重即时看效果"的动力来源） */
export function previewLine(
  profile: Partial<LineProfile>,
  signal?: AbortSignal,
): Promise<PreviewResponse> {
  return request<PreviewResponse>(`${PREFIX}/preview`, {
    method: 'POST',
    body: profile,
    signal,
  })
}

/** 只校验档案并返回问题列表（不保存） */
export function validateLine(profile: Partial<LineProfile>): Promise<ValidateResponse> {
  return request<ValidateResponse>(`${PREFIX}/validate`, { method: 'POST', body: profile })
}

// ═══════════════════════════════════════════════════════════
//  写（后端 require_token；删除另需 confirm）
// ═══════════════════════════════════════════════════════════

/** 新建档案（id 已存在时后端返回 409，绝不静默覆盖） */
export function createLine(
  profile: Partial<LineProfile>,
): Promise<{ ok: boolean; line: LineProfile; created: string }> {
  return request(`${PREFIX}`, { method: 'POST', body: profile })
}

/** 保存 / 覆盖档案（路径即身份；body.id 与路径冲突时后端 400） */
export function saveLine(
  lineId: string,
  profile: Partial<LineProfile>,
): Promise<{ ok: boolean; line: LineProfile; created: boolean; note: string }> {
  return request(`${PREFIX}/${encodeURIComponent(lineId)}`, {
    method: 'PUT',
    body: profile,
  })
}

/** 删除档案（破坏性：后端要求 `confirm: true`，此处固定带上） */
export function deleteLine(
  lineId: string,
): Promise<{ ok: boolean; deleted: string; active: string | null; note: string }> {
  return request(`${PREFIX}/${encodeURIComponent(lineId)}`, {
    method: 'DELETE',
    body: { confirm: true },
  })
}

/** 设置当前激活主线；`null` = 不装线（全量工具） */
export function setActiveLine(
  lineId: string | null,
): Promise<{ ok: boolean; active: string | null; note: string }> {
  return request(`${PREFIX}/active`, { method: 'POST', body: { line_id: lineId } })
}
