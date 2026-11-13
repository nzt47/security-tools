/**
 * 报警器接口（/api/alarms/*）
 * ================================================================
 * 【为什么它是独立 api 文件，而不是复用 utils/request】
 * utils/request 的响应拦截在 401 时会**清 token 并跳登录页** —— 对报表/管理页合适，
 * 对"顶栏常驻读数"是灾难：令牌缺失时铃铛应当降级隐藏，而不是把用户踢下线。
 * 这里用裸 fetch + getApiToken()，失败只抛错给调用方（组件自行降级）。
 *
 * 数据源说明：顶栏铃铛与消息中心读的是**同一张事件台账**（后端 /api/alarms），
 * 不是两套未读；消息中心的历史列表来自同一台账的分页投影。
 */
import { getApiToken } from '@/lib/apiToken'
import {
  ALARMS_COVERAGE,
  ALARMS_LIST,
  ALARMS_READ_ALL,
  ALARMS_RULES,
  ALARMS_UNREAD,
  alarmAck,
  alarmRead,
  alarmSilence,
} from '@/api/endpoints'

export type AlarmSeverity = 'info' | 'warning' | 'critical'
export type AlarmState = 'firing' | 'resolved'

export interface AlarmItem {
  id: number
  key: string
  kind: string
  severity: AlarmSeverity
  title: string
  detail?: string
  subject?: string
  source?: string
  state: AlarmState
  first_seen?: string
  last_seen?: string
  resolved_at?: string
  count?: number
  read?: boolean
  acked?: boolean
  acked_by?: string
  acked_at?: string
  silenced_until?: string
}

export interface AlarmSummary {
  total: number
  unread: number
  firing: number
  resolved: number
  firing_by_severity: Record<string, number>
}

export interface AlarmListPayload {
  items: AlarmItem[]
  total: number
  limit: number
  offset: number
  summary: AlarmSummary
}

export interface AlarmListParams {
  state?: AlarmState
  unreadOnly?: boolean
  kind?: string
  limit?: number
  offset?: number
}

// 【路径一律来自端点常量层】本仓纪律：React 侧**不得**出现常量层之外的 /api 字面量
// （守卫 tests/unit/test_frontend_stray_baseline.py 要求恰好 0 处；常量层是唯一允许的
// 落字面量处，见 src/api/endpoints.ts 头注）。所以这里用 ALARMS_* / alarmXxx()，
// 不自己拼 /api/alarms/... —— 那会同时踩字面量守卫与「找不到该改哪些前端文件」的老坑。

/** 统一解析 {code,data} 信封；非 200 业务码抛错（错误文案由后端给，前端不编） */
async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getApiToken()
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) }
  if (token) headers.Authorization = 'Bearer ' + token
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, { ...init, headers })
  let body: { code?: number; data?: T; message?: string } | null = null
  try {
    body = (await res.json()) as { code?: number; data?: T; message?: string }
  } catch {
    body = null
  }
  if (!res.ok || !body || (body.code !== undefined && body.code !== 200)) {
    throw new Error((body && body.message) || 'HTTP ' + res.status)
  }
  return body.data as T
}

/** 报警列表（当前态）。unreadOnly 只回未读且不在静默期的 */
export function listAlarms(params: AlarmListParams = {}): Promise<AlarmListPayload> {
  const q = new URLSearchParams()
  if (params.state) q.set('state', params.state)
  if (params.unreadOnly) q.set('unread_only', '1')
  if (params.kind) q.set('kind', params.kind)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const suffix = q.toString() ? '?' + q.toString() : ''
  return call<AlarmListPayload>(ALARMS_LIST + suffix)
}

/** 未读数（静默中的不计入） */
export function getUnreadAlarms(): Promise<{ unread: number; summary: AlarmSummary }> {
  return call<{ unread: number; summary: AlarmSummary }>(ALARMS_UNREAD)
}

/** 标记一条已读 */
export function markAlarmRead(id: number): Promise<{ id: number; read: boolean }> {
  return call<{ id: number; read: boolean }>(alarmRead(id), { method: 'POST' })
}

/** 全部标记已读 */
export function markAllAlarmsRead(): Promise<{ updated: number }> {
  return call<{ updated: number }>(ALARMS_READ_ALL, { method: 'POST' })
}

/** 确认（表示"人已知悉"，与已读分开记） */
export function ackAlarm(id: number, by = 'owner'): Promise<{ id: number; acked: boolean }> {
  return call<{ id: number; acked: boolean }>(alarmAck(id), {
    method: 'POST',
    body: JSON.stringify({ by }),
  })
}

// ── 监控覆盖 / 规则覆盖率（2026-11-11，alarms-coverage-in-ui）──────────────
//  这两条读数此前只有端点、没有 UI 入口（只能在排障时手工调），于是"到底看得到什么、
//  看不到什么"对使用者不可见。现在接进健康仪表盘的「告警」Tab 顶部。
//  口径：算不了的规则如实列原因（缺哪个指标 / 样本不足 / 需 Prometheus），
//  **不写成「未触发」**——与 L5「没测到不等于正常」同一条。

/** 事件源（六条） */
export interface AlarmCoverageSource {
  id: string
  name: string
  detail: string
}

export interface AlarmRuleBlocked {
  alert: string
  state: string
  state_label?: string
  reason?: string
}

export interface AlarmRulesCoverage {
  rules_total: number
  rules_evaluable: number
  rules_blocked: number
  by_state: Record<string, number>
  blocked: AlarmRuleBlocked[]
}

export interface AlarmCoverage {
  sources: AlarmCoverageSource[]
  rules: AlarmRulesCoverage
  /** 上一轮采集：各源是否真的读到了数据（false = 本轮不可用 ⇒ 该源不报警也不判恢复） */
  last_collect?: {
    checked?: Record<string, boolean>
    raised?: number
    resolved?: number
    kept?: string[]
    warmup?: boolean
  }
  /** 外部 Prometheus（可选）：未配置时为 {configured:false, ...} */
  prometheus?: {
    configured: boolean
    last_ok: boolean | null
    detail: string
  } | null
  /** 静默缺口①（2026-11-12）：CI 事件源的最后同步时间 —— 过期就是过期，不写成"正常" */
  ci?: AlarmCoverageCi | null
  /** 静默缺口②（2026-11-12）：感知周期采集的最后事件时间 —— "没在感知"不能读成"没变化" */
  perception?: AlarmCoveragePerception | null
  /** 覆盖缺口自检（2026-11-12，monitoring-gap-detector）：新增能力有没有被感知/监控/告警覆盖 */
  gaps?: AlarmCoverageGaps | null
}

/** 覆盖缺口（后端 /api/alarms/coverage.gaps；每小时自检一轮，页面只读结果） */
export interface AlarmCoverageGap {
  /** package / unit / sensor / scheduler / route / tool */
  kind: string
  id: string
  /** 为什么算缺口（可解释判据，不是"猜"） */
  why: string
  /** 建议补哪一处 */
  fix: string
}

export interface AlarmCoverageGaps {
  /** 有状态文件才算可用；false 时 count 为 null（**不是 0**，L5：没测到 ≠ 0 条） */
  available: boolean
  count: number | null
  by_kind?: Record<string, number>
  gaps?: AlarmCoverageGap[]
  truncated?: boolean
  /** true = 有输入面读不到 ⇒ 本轮清单不完整 */
  degraded?: boolean
  generated_at?: string | null
  age_seconds?: number | null
  stale?: boolean
  pushed?: number
  detail?: string
}

/** CI 事件源覆盖读数（后端 /api/alarms/coverage.ci） */
export interface AlarmCoverageCi {
  available: boolean
  last_sync_at?: string | null
  age_seconds?: number | null
  /** true = 超过 2h 阈值（采集层据此把 CI 源当"不可用"，既不报警也不判恢复） */
  stale: boolean
  max_age_seconds?: number
  runs?: number
  detail?: string
}

/** 感知周期采集覆盖读数（后端 /api/alarms/coverage.perception） */
export interface AlarmCoveragePerception {
  available: boolean
  watch_dirs?: string[]
  watcher_running?: boolean
  last_tick_at?: string | null
  last_tick_age_seconds?: number | null
  last_file_event_at?: string | null
  last_file_event_age_seconds?: number | null
  last_change_at?: string | null
  last_novelty_at?: string | null
  last_novelty_age_seconds?: number | null
  long_silent?: boolean
  detail?: string
}

export interface AlarmRuleState {
  alert: string
  expr?: string
  severity?: string
  state?: string
  state_label?: string
  value?: number | null
  reason?: string
  evaluated_at?: number
}

export interface AlarmRulesPayload {
  rules: AlarmRuleState[]
  coverage: AlarmRulesCoverage
  /** 采样环点数（窗口类规则要有两个点以上才算得出来） */
  samples?: number | null
}

/** 监控覆盖：事件源清单 + 上一轮采集统计 + 可选 Prometheus 状态 */
export function getAlarmCoverage(): Promise<AlarmCoverage> {
  return call<AlarmCoverage>(ALARMS_COVERAGE)
}

/** 规则求值状态：逐条 state/reason + 覆盖率 + 采样环点数 */
export function getAlarmRules(): Promise<AlarmRulesPayload> {
  return call<AlarmRulesPayload>(ALARMS_RULES)
}

/** 静默 N 分钟（不改状态、不删除；到期自动重新计入未读） */
export function silenceAlarm(id: number, minutes: number): Promise<{ id: number; silenced_until: string }> {
  return call<{ id: number; silenced_until: string }>(alarmSilence(id), {
    method: 'POST',
    body: JSON.stringify({ minutes }),
  })
}
