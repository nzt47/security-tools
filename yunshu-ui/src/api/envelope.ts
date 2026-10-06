/**
 * 显式信封解析（P1-front 的**收口点**）。
 *
 * 【解决什么】页面层此前用 \`pickObj/pickList\`（见 pages/hub/components/ui.tsx）
 * **猜**响应形态：它们先看有没有 \`data\`，没有就把整个响应当成业务对象。那不是契约，
 * 是启发式 —— 后端换一种包法，前端会**静默读错**而不是报错。
 *
 * 【为什么可以开始删启发式了】后端已在端点级逐步补上 \`X-Envelope: v2\`
 * （第一批：/api/heartbeat 三件套）。凡是带上该头的端点，前端就能**显式判断**形态，
 * 于是可以按端点换成这里的显式解析；`pickObj/pickList` 的适用范围随之缩小，
 * 最终（全部端点铺完后）可以整段删除。
 *
 * 【纪律：不做"兼容两态"的宽解析】本模块**只认信封**。没带头就是契约没到位 ——
 * 抛错并说清原因，好过猜一个形状然后把错误推到很远的地方（本仓对"静默错"的
 * 记录多于对"响亮失败"的记录）。迁移是**逐端点**的：没迁移的端点继续用旧的
 * 启发式 helper，两边互不影响。
 */

import { authHeader } from '../lib/apiToken'

/** 后端统一信封（成功侧）：\`{code, data, message}\`，可带 \`meta\`。 */
export interface Envelope<T> {
  code: number
  data: T
  message?: string
  meta?: Record<string, unknown>
}

/** 信封版本头的名字与当前版本（与 agent/api_envelope.py 的常量同一份契约）。 */
export const ENVELOPE_HEADER = 'X-Envelope'
export const ENVELOPE_VERSION = 'v2'

export class EnvelopeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'EnvelopeError'
  }
}

/**
 * 从**已解析好的响应体**里取出信封载荷；体不是信封就抛错。
 *
 * 【为什么单独有这个函数】页面层用 getEnvelope/postEnvelope（自己发请求）；
 * 而 lib/apiClient.request() 那条链路（lib/contextMonitorApi.ts 等）**自己发请求并返回原始体**，
 * 且它不拆信封 —— 那种消费方需要一个「拿已解析的体来拆」的入口，
 * 否则它会在 {code, data, message} 上读业务键而**静默拿到 undefined**。
 *
 * @param body 已经 JSON.parse 过的响应体（或 request() 的返回值）
 * @param endpoint 仅用于报错信息
 * @throws EnvelopeError 体不是统一信封 / 业务码非 200
 */
export function unwrapEnvelopeBody<T>(body: unknown, endpoint: string): T {
  const env = body as Partial<Envelope<T>> | null
  if (env === null || typeof env !== 'object' || typeof env.code !== 'number') {
    throw new EnvelopeError(
      endpoint + ' 的响应体不是统一信封（缺数字型 code）—— 该端点尚未迁移到统一信封，'
      + '或迁移被回退，或调用处拿错了响应。实测：' + JSON.stringify(body)?.slice(0, 200),
    )
  }
  if (env.code !== 200) {
    throw new EnvelopeError(endpoint + ' 返回业务码 ' + env.code + '（消息：' + (env.message || '') + '）')
  }
  return env.data as T
}

/**
 * 从 \`fetch\` 的 Response 读取**已声明信封**的成功载荷。
 *
 * @param res fetch 的 Response（须先 \`await\`，本函数读 header 与 body）
 * @param endpoint 仅用于报错信息，让人一眼看出是哪个端点没带信封
 * @throws EnvelopeError 响应没带信封头 / 不是 JSON / 业务码非 200
 */
export async function readEnvelope<T>(res: Response, endpoint: string): Promise<T> {
  const version = res.headers.get(ENVELOPE_HEADER)
  if (version === null) {
    throw new EnvelopeError(
      endpoint + ' 的响应没有 ' + ENVELOPE_HEADER + ' 头 —— 该端点尚未迁移到统一信封，'
      + '或迁移被回退。请勿在未迁移的端点上使用显式解析（会读到 undefined）；'
      + '修复方式：后端该视图改用 agent.api_envelope.ok()。HTTP ' + res.status,
    )
  }
  if (version !== ENVELOPE_VERSION) {
    throw new EnvelopeError(
      endpoint + ' 的信封版本是 ' + version + '，本前端按 ' + ENVELOPE_VERSION
      + ' 解析。版本变了就要同步更新解析层，不要让它按旧版读新版。',
    )
  }
  let body: unknown
  try {
    body = await res.json()
  } catch {
    throw new EnvelopeError(endpoint + ' 声明了信封头但不是合法 JSON')
  }
  return unwrapEnvelopeBody<T>(body, endpoint)
}

/** GET + 显式信封解析。用于**已迁移**的只读端点。
 *
 * 【令牌行为与 hubGet 对齐】不显式传 token 时自动附带本地保存的 API 令牌
 * （与 pages/hub/components/ui.tsx::hubGet 同一语义，避免迁移解析方式时
 *  顺带把鉴权行为改掉 —— 那会是"迁移里的夹带变更"）。
 */
export async function getEnvelope<T>(url: string, token?: string | null): Promise<T> {
  const headers: Record<string, string> = token ? { Authorization: 'Bearer ' + token } : authHeader()
  const res = await fetch(url, { headers })
  if (!res.ok) throw new Error('HTTP ' + res.status)
  return readEnvelope<T>(res, url)
}

/** POST + 显式信封解析。用于**已迁移**的写端点（第六批起有 POST 端点迁入）。
 *
 * 【与 hubPost 的语义逐条对齐 —— 迁移解析方式不得顺带改行为】
 *  · 未显式传 token 时自动附带本地保存的 API 令牌（与 hubGet/hubPost/getEnvelope 同）；
 *  · **只在确有 body 时才声明 `Content-Type: application/json`**：空 body 配 JSON 头会让
 *    后端的 `request.get_json()` 抛 415/400（本仓已实测过这一条，见 hubPost 的注释），
 *    故这里逐字保留同一判断，不因为「反正现在都有 body」而简化。
 *
 * 【为什么错误体不在这里解析】与 getEnvelope 同：非 2xx 直接抛 HTTP 状态 ——
 * 错误侧的统一（RFC 9457）是另一条线，不在本模块。
 */
export async function postEnvelope<T>(url: string, body?: unknown, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined && body !== null) headers['Content-Type'] = 'application/json'
  Object.assign(headers, token ? { Authorization: 'Bearer ' + token } : authHeader())
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error('HTTP ' + res.status)
  return readEnvelope<T>(res, url)
}
