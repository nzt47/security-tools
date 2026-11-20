/**
 * LLM 禁用时段 API（/api/llm-gate）
 * ================================================================
 * 顶栏开关与时段配置共用这一份读写：GET 取配置 + 运行状态，POST 部分更新。
 *
 * 【为什么用裸 fetch 而不是 utils/request】与 AlarmBell 同因：顶栏常驻控件在
 * 令牌缺失时应当降级提示，而不是把用户踢下线（utils/request 的 401 会跳登录）。
 */
import { getApiToken } from '@/lib/apiToken'
import { LLM_GATE } from '@/api/endpoints'

export interface GateWindow {
  /** 星期：0=周一 … 6=周日 */
  days: number[]
  start: string
  end: string
}

export interface GateConfig {
  enabled: boolean
  windows: GateWindow[]
  /** true = 法定节假日不拦截（按空闲处理） */
  skip_holidays: boolean
  /** 提前量（秒）：临近禁用时段时的收尾提示/任务顺延阈值 */
  lead_seconds: number
}

export interface GateState {
  enabled: boolean
  blocked: boolean
  now: string
  weekday_cn?: string
  holiday?: boolean
  window?: GateWindow | null
  next_block?: string | null
  next_unblock?: string | null
  seconds_until_blocked?: number | null
  message: string
}

export interface GatePayload {
  ok: boolean
  config: GateConfig
  state: GateState
}

async function call(init: RequestInit = {}): Promise<GatePayload> {
  const token = getApiToken()
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) }
  if (token) headers.Authorization = 'Bearer ' + token
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(LLM_GATE, { ...init, headers })
  let body: GatePayload | null = null
  try {
    body = (await res.json()) as GatePayload
  } catch {
    body = null
  }
  if (!res.ok || !body || !body.ok) {
    throw new Error((body && (body as unknown as { error?: string }).error) || 'HTTP ' + res.status)
  }
  return body
}

export function getLlmGate(): Promise<GatePayload> {
  return call()
}

export function setLlmGate(patch: Partial<GateConfig>): Promise<GatePayload> {
  return call({ method: 'POST', body: JSON.stringify(patch) })
}
