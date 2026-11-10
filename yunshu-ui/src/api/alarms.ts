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
  ALARMS_LIST,
  ALARMS_READ_ALL,
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

/** 静默 N 分钟（不改状态、不删除；到期自动重新计入未读） */
export function silenceAlarm(id: number, minutes: number): Promise<{ id: number; silenced_until: string }> {
  return call<{ id: number; silenced_until: string }>(alarmSilence(id), {
    method: 'POST',
    body: JSON.stringify({ minutes }),
  })
}
