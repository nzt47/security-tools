/**
 * 治理可观测面板 API 客户端（v7.2 §7）
 * ------------------------------------------------------------------
 * 契约来源：`agent/server_routes/routes_ui_panels.py`（前缀 `/api/cp`）。
 *
 * 【设计取舍】
 *   - 复用既有 `lib/apiClient.request`（fetch + 自动附带 `Authorization: Bearer`，
 *     见 `lib/apiToken.ts`），**不新建 HTTP 层**；
 *   - 所有读取都是 GET，所有写动作只有两个入口：
 *     `issueConfirmation()`（签发 §5.7 机制 5 的 UI 单次确认凭据）
 *     与 `runAction()` / `batchLink()` / `batchDecide()`（写动作，一律走后端审批）；
 *   - 前端**不拥有额外权限**：actor / actor_type 一律不由此客户端声明。
 */

import { request, buildQuery } from './apiClient'
import type {
  ActionName,
  ActionResponse,
  ApprovalInboxView,
  AuditExportView,
  AuthzAlertsView,
  BatchDecisionResult,
  CapabilityMapView,
  ConfirmationIssue,
  IncidentsView,
  MemorySkillsView,
  ObservabilityStreamView,
  PanelsIndex,
  PipelineView,
  RoiView,
  SecurityRenderState,
  SettingsChangeBody,
  SettingsChangeResponse,
  SettingsConfirmBody,
  SettingsView,
} from './cpPanelsTypes'

const PREFIX = '/api/cp'

// ═══════════════════════════════════════════════════════════
//  只读面板
// ═══════════════════════════════════════════════════════════

/** 面板索引（优先级 + 数据源台账；验收与"来源"入口共用） */
export function fetchPanels(): Promise<PanelsIndex> {
  return request<PanelsIndex>(`${PREFIX}/panels`)
}

/** 消化流水线（P0）：泳道五列 + 灰度 + 内化决策 + 抽检状态 */
export function fetchPipeline(
  params: { days?: number; limit?: number } = {},
): Promise<PipelineView> {
  const q = buildQuery({ days: params.days, limit: params.limit })
  return request<PipelineView>(`${PREFIX}/digestion/pipeline${q ? `?${q}` : ''}`)
}

/** 能力地图（P1）：capability × provenance / risk / data_class / stage */
export function fetchCapabilityMap(
  params: {
    stage?: string
    provenance?: string
    risk?: string
    data_class?: string
    q?: string
    limit?: number
    offset?: number
  } = {},
): Promise<CapabilityMapView> {
  const q = buildQuery(params)
  return request<CapabilityMapView>(`${PREFIX}/descriptors/map${q ? `?${q}` : ''}`)
}

/** 审批收件箱（P0）：待审 + 批量裁决分组 + 气泡可见性 */
export function fetchApprovalInbox(
  params: { limit?: number; object_type?: string } = {},
): Promise<ApprovalInboxView> {
  const q = buildQuery(params)
  return request<ApprovalInboxView>(`${PREFIX}/approvals/inbox${q ? `?${q}` : ''}`)
}

/** ROI / 成本（P1）：月省/投入/阈值 + ACR/UTC + 审批衰减率 + §6.7 八项指标 */
export function fetchRoi(params: { days?: number } = {}): Promise<RoiView> {
  const q = buildQuery({ days: params.days })
  return request<RoiView>(`${PREFIX}/roi${q ? `?${q}` : ''}`)
}

/** 事件流消费（S2-03 #12）：ACR/UTC + 降级拓扑 + 逃逸清单 */
export function fetchObservabilityStream(
  params: { days?: number; limit?: number } = {},
): Promise<ObservabilityStreamView> {
  const q = buildQuery({ days: params.days, limit: params.limit })
  return request<ObservabilityStreamView>(`${PREFIX}/observability/stream${q ? `?${q}` : ''}`)
}

/** 自愈事故（P1）：事故卡 + MTTD/MTTR + 备份健康 */
export function fetchIncidents(
  params: { limit?: number; days?: number } = {},
): Promise<IncidentsView> {
  const q = buildQuery(params)
  return request<IncidentsView>(`${PREFIX}/healing/incidents${q ? `?${q}` : ''}`)
}

/** 记忆 / 技能库（P2）：四层召回 + U5 优先级契约 */
export function fetchMemorySkills(
  params: { layers?: string; tenant_id?: string; q?: string; limit?: number } = {},
): Promise<MemorySkillsView> {
  const q = buildQuery(params)
  return request<MemorySkillsView>(`${PREFIX}/memory/skills${q ? `?${q}` : ''}`)
}

/** 越权告警聚合（U3）：实时口径（易失）+ 耐久口径（事件流） */
export function fetchAuthzAlerts(
  params: { limit?: number; days?: number } = {},
): Promise<AuthzAlertsView> {
  const q = buildQuery(params)
  return request<AuthzAlertsView>(`${PREFIX}/security/authz-alerts${q ? `?${q}` : ''}`)
}

/** ★ U1：安全渲染 / 边界词常量（**前端唯一来源**，不得自定义） */
export function fetchSecurityRenderState(): Promise<SecurityRenderState> {
  return request<SecurityRenderState>(`${PREFIX}/security/render-state`)
}

/** 审计导出（含验签摘要） */
export function fetchAuditExport(
  params: {
    limit?: number
    start_seq?: number
    end_seq?: number
    action?: string
    actor?: string
    day?: string
    verify?: boolean
    verify_scope?: 'exported' | 'full' | 'head'
  } = {},
): Promise<AuditExportView> {
  const q = buildQuery(params)
  return request<AuditExportView>(`${PREFIX}/audit/export${q ? `?${q}` : ''}`)
}

/** 审计导出 CSV 的下载 URL（浏览器直接下载；令牌由 <a> 无法携带 ⇒ 用 downloadAuditCsv） */
export function auditCsvUrl(params: Record<string, unknown> = {}): string {
  const q = buildQuery(params)
  return `${PREFIX}/audit/export.csv${q ? `?${q}` : ''}`
}

/** 下载审计 CSV（带上鉴权头，走 fetch → Blob，避免 <a> 丢令牌） */
export async function downloadAuditCsv(
  params: Record<string, unknown> = {},
): Promise<{ filename: string; blob: Blob }> {
  const { authHeader } = await import('./apiToken')
  const res = await fetch(auditCsvUrl(params), { headers: { ...authHeader() } })
  if (!res.ok) throw new Error(`导出失败：HTTP ${res.status}`)
  const blob = await res.blob()
  return { filename: 'cp_audit_export.csv', blob }
}

// ═══════════════════════════════════════════════════════════
//  写动作（一律走后端既有审批；前端不拥有额外权限）
// ═══════════════════════════════════════════════════════════

/** 签发「永不自动化五类」的单次确认凭据（60s 硬上限，**不接受 ttl 参数**） */
export function issueConfirmation(
  action: string,
  body: { target?: string; action_text?: string; payload?: Record<string, unknown> },
): Promise<ConfirmationIssue> {
  return request<ConfirmationIssue>(
    `${PREFIX}/confirmations/${encodeURIComponent(action)}`,
    { method: 'POST', body },
  )
}

export interface RunActionBody {
  target?: string
  reason?: string
  risk?: string
  second_factor?: string
  confirmation_token?: string
  action_text?: string
  payload?: Record<string, unknown>
  components?: string[]
  dry_run?: boolean
  approval_record_id?: string
  tenant_id?: string
}

/** 七动作统一入口（只读动作也走这里，返回 `read_only: true`） */
export function runAction(
  action: ActionName | string,
  body: RunActionBody = {},
): Promise<ActionResponse> {
  return request<ActionResponse>(
    `${PREFIX}/actions/${encodeURIComponent(action)}`,
    { method: 'POST', body },
  )
}

/** 批量裁决第一段：为所选记录签发逐条绑定的一次性审批链接 */
export function batchLink(recordIds: string[]): Promise<{
  ok: boolean
  batch_id: string
  record_ids: string[]
  batch_key: string
  ttl_seconds: number
  one_time_per_record: boolean
  note: string
}> {
  return request(`${PREFIX}/approvals/batch/link`, {
    method: 'POST',
    body: { record_ids: recordIds },
  })
}

/** 批量裁决第二段：提交裁决（逐条走同一审批链） */
export function batchDecide(body: {
  batch_id: string
  decision: 'approve' | 'reject'
  reason?: string
  second_factor?: Record<string, string>
}): Promise<BatchDecisionResult> {
  return request<BatchDecisionResult>(`${PREFIX}/approvals/batch`, {
    method: 'POST',
    body,
  })
}

// ═══════════════════════════════════════════════════════════
//  开关中心（TASK-S7-01「开关中心」）
// ═══════════════════════════════════════════════════════════

/**
 * 开关登记表全量读取（≈300 项）
 *
 * 【契约要点（FROZEN）】
 *   - `source` ∈ env | ui_override | config | default；`shadowed_by` 是"存在但未生效"的低优先级来源；
 *   - `secret=true` 时后端只给 `value: null` + `masked: true` + **已脱敏的** `display_value`
 *     与 `configured` 布尔——前端**永不期望明文、永不自行拼装掩码串**；
 *   - `locked=true ⇒ editable=false`，且 `locked_reason` 必须原样上屏。
 */
export function fetchSettings(): Promise<SettingsView> {
  return request<SettingsView>(`${PREFIX}/settings`)
}

/**
 * 变更单个开关（**无批量端点**：数组体一律 `400 batch_not_supported`）
 *
 * 200 ⇒ 已生效（`applied:true`，`source` 通常转为 `ui_override`）；
 * 202 ⇒ `pending:true` + `pending_id`，**需第二位人工**走 `confirmSetting()`；
 * 4xx ⇒ `ApiError`（`code` + `message` + 可选 `decision`，见 `settingsDenialMessage()`）。
 */
export function changeSetting(
  key: string,
  body: SettingsChangeBody,
): Promise<SettingsChangeResponse> {
  return request<SettingsChangeResponse>(
    `${PREFIX}/settings/${encodeURIComponent(key)}`,
    { method: 'POST', body },
  )
}

/** B 级开关的第二位人工确认（`pending_id` 来自 202 响应；**不能由同一人自查自批**） */
export function confirmSetting(
  key: string,
  body: SettingsConfirmBody,
): Promise<SettingsChangeResponse> {
  return request<SettingsChangeResponse>(
    `${PREFIX}/settings/${encodeURIComponent(key)}/confirm`,
    { method: 'POST', body },
  )
}

/** 回滚到默认（`source` 回到 `default`；也用于撤销 `ui_override`） */
export function resetSetting(key: string): Promise<SettingsChangeResponse> {
  return request<SettingsChangeResponse>(
    `${PREFIX}/settings/${encodeURIComponent(key)}/reset`,
    { method: 'POST' },
  )
}
