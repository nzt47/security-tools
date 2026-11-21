/**
 * 主权分身 API 客户端 + 组装台**唯一**请求体序列化器
 * ------------------------------------------------------------------
 * 契约来源：`agent/server_routes/routes_subagent.py`（前缀 `/api/subagent`）。
 *
 * 【为什么把 buildCreatePayload 放在这里而不是组件里】
 *   组装台同时要展示「装配预览」和真的发「创建请求」。两处各拼一份 body，
 *   迟早分叉 —— 而"分叉的预览"比没有预览更坏：它会让人以为已经看过了。
 *   因此序列化只有一个纯函数，预览与请求**共用它**；守卫见 assembly.test.tsx
 *   （把预览文本与真实 POST body 做深比较，回退成分开拼立刻红）。
 *
 * 【为什么清单数据由后端给】`fetchSovereignty()` 直接取 `capabilities.py` 的三态
 *   投影；前端不自己判"某字段是否已接线"——那是第二份口径。
 */

import { request } from './apiClient'
import { SUBAGENT_CAPABILITIES } from '@/api/endpoints'
import { normalizeTopics } from '@/lib/topics'  // 词表唯一来源（与后端 agent/topics.py 对拍）

// ═══════════════════════════════════════════════════════════
//  主权清单（与 agent/subagent/capabilities.py 的投影对齐）
// ═══════════════════════════════════════════════════════════

export type SovereigntyState = 'owned' | 'partial' | 'missing'

export interface SovereigntyFace {
  key: string
  layer: string
  label: string
  question: string
  state: SovereigntyState
  /** 后端派生的人读标签（拥有 / 声明未接线 / 未做）——前端不另立 */
  state_label: string
  evidence: string
  evidence_files: string[]
  gap: string
  /** 计划补齐的阶段（S3/S4/S5）；owned 的残余边界也可能标阶段 */
  next_stage: string
}

export interface SovereigntyLayer {
  key: string
  label: string
  question: string
  /** 该层包含的面键（有序） */
  faces: string[]
}

export interface MaturityLevel { key: string; label: string; note: string }

export interface SovereigntyReport {
  layers: SovereigntyLayer[]
  faces: SovereigntyFace[]
  summary: { owned: number; partial: number; missing: number; total: number }
  maturity: { levels: MaturityLevel[]; current: string }
}

interface CapabilitiesPayload { ok?: boolean; capabilities?: SovereigntyReport }

/** 取主权清单（只读投影；失败时抛出，由调用方显示错误态） */
export async function fetchSovereignty(): Promise<SovereigntyReport> {
  const res = await request<CapabilitiesPayload>(SUBAGENT_CAPABILITIES)
  const report = res?.capabilities
  if (!report || !Array.isArray(report.faces)) {
    throw new Error('主权清单载荷异常：缺少 capabilities.faces')
  }
  return report
}

// ═══════════════════════════════════════════════════════════
//  角色档位兜底（与 agent/subagent/role_templates.ROLE_TIERS 同序同值）
// ═══════════════════════════════════════════════════════════

export const DEFAULT_ROLE_TIERS = [
  { value: 'template', label: '受控模板', red: false },
  { value: 'template+text', label: '模板 + 自由文本（进约束）', red: false },
  { value: 'full-system', label: '自由文本进系统提示词（红档）', red: true },
]

// ═══════════════════════════════════════════════════════════
//  组装台：唯一请求体序列化器
// ═══════════════════════════════════════════════════════════

export interface AssembleForm {
  name: string
  roleTemplate: string
  roleMode: string
  roleText: string
  modelId: string
  temperature: string
  memoryProvider: string
  /** 记忆档位：'' = 不表态（后端默认 none）；当前只提供 none/brokered */
  memoryMode: string
  /** 可装载的主题类（仅在 brokered 下随 memory_scope 发出） */
  topics: string[]
  toolSources: string
  permissions: string[]
  ttlSeconds: string
}

/** 组装台的初始（空白）表单：全部取"最小权限 / 不干预"的默认值 */
export const EMPTY_ASSEMBLE_FORM: AssembleForm = {
  name: '',
  roleTemplate: '',
  roleMode: 'template',
  roleText: '',
  modelId: '',
  temperature: '',
  memoryProvider: 'default',
  memoryMode: '',
  topics: ['general'],
  toolSources: '',
  permissions: ['read'],
  ttlSeconds: '',
}

/**
 * 表单 → `POST /api/subagent/create` 请求体（唯一权威；预览与请求共用）。
 *
 * 三条"不发这个键"的纪律（发了就等于替使用者表态）：
 *   · 温度留空 ⇒ 不发 `llm_temperature`（None = 不干预执行器默认，不是 0.0）；
 *   · 自由文本留空 ⇒ 不发 `role_text`（默认档下发非空值后端会 400）；
 *   · TTL 留空 ⇒ 不发 `ttl_seconds`（0 = 永久，是后端默认）。
 */
export function buildCreatePayload(form: AssembleForm): Record<string, unknown> {
  const sources = form.toolSources
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
  const memoryMode = form.memoryMode === 'brokered' ? 'brokered' : ''
  const topics = normalizeTopics(form.topics)
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    model_id: form.modelId.trim(),
    memory_provider: form.memoryProvider.trim() || 'default',
    tool_sources: sources,
    permissions: form.permissions.length ? form.permissions : ['read'],
    tags: ['hub', 'assembly-console'],
    role_template: form.roleTemplate.trim(),
    role_mode: form.roleMode,
  }
  const temperature = form.temperature.trim()
  if (temperature) body.llm_temperature = Number(temperature)
  const roleText = form.roleText.trim()
  if (roleText) body.role_text = roleText
  const ttl = form.ttlSeconds.trim()
  if (ttl) body.ttl_seconds = Number(ttl)
  // 记忆档：只有选了才发（不表态 = 后端默认 none）
  if (memoryMode) body.memory_mode = memoryMode
  // 可装载的主题类走**顶层** memory_topics（与记忆档位无关；none 档也能声明）。
  // 【为什么不发 general】只有 general 与"不发"在语义上等价（未声明 ⇒ 只读 general），
  // 发了只会给每个分身写一条冗余声明。
  const onlyDefault = topics.length === 1 && topics[0] === 'general'
  if (!onlyDefault) body.memory_topics = topics
  return body
}

// ═══════════════════════════════════════════════════════════
//  编辑已有分身的角色：热更新请求体（POST /api/subagent/<name>/reload）
// ═══════════════════════════════════════════════════════════

/**
 * 角色编辑草稿（弹窗里的即时状态，不是后端配置）。
 *
 * [为什么单独一个类型] 弹窗要区分"人没动正文"与"人要清空正文"——两者在
 * 请求体里是**完全不同**的（不发键 vs 发空串），而在输入框里都表现为"空"。
 */
export interface RoleDraft {
  /** 受控模板 id；空 = 不装角色（system prompt 里没有角色片段，旧行为） */
  template: string
  /** 档位：template / template+text / full-system */
  mode: string
  /** 本次要写入的自由文本（trim 后为空 = 交给人选的"保留 / 清空"决定） */
  text: string
  /** 显式清空该分身现有的自由文本（正文不回显，所以不能靠"留空"猜意图） */
  clearText: boolean
}

/**
 * 草稿 → `POST /api/subagent/<name>/reload` 的角色三字段（**唯一**序列化口径）。
 *
 * 后端规矩（agent/server_routes/routes_subagent.py::api_subagent_reload）：
 * **没传的字段沿用存活容器的现值** —— 这条是防止"改个模板顺手把 role_text 抹了"。
 * 所以本函数的三条分支必须与它对齐：
 *
 *  1. 有输入正文 ⇒ 发 `role_text`（替换）；
 *  2. 没输入正文 ⇒ **默认不发这个键**（保留现有正文）；
 *  3. 但三种情况必须显式发空串，否则永远切不回去：
 *     · 选了「不装角色」（无模板时自由文本没有可依附的受控来源，后端会 400）；
 *     · 档位切回 `template`（默认档配非空正文后端 400）；
 *     · 人勾了「清空现有自由文本」。
 *
 * [为什么不在这里判长度/词表] 那两条是后端的权威校验（400 + error_code），
 * 前端只做提示；在这里再判一次就是第二份口径。
 */
export function buildRoleUpdatePayload(draft: RoleDraft): Record<string, unknown> {
  const template = String(draft.template || '').trim()
  // 没有模板时档位无意义：后端 normalize_tier 会照收，但存一个 full-system 的空角色
  // 只会让人以后误读 —— 归一为默认档。
  const mode = template ? String(draft.mode || 'template') : 'template'
  const text = String(draft.text || '').trim()
  const body: Record<string, unknown> = { role_template: template, role_mode: mode }
  if (text) body.role_text = text
  else if (!template || mode === 'template' || draft.clearText) body.role_text = ''
  return body
}