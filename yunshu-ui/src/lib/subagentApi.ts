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
  return body
}