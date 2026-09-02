/**
 * 【生成日志摘要】
 * - 生成时间：2026-06-28
 * - 内容：rrweb 用户行为录制管理器（v1）
 * - 参数：采样率 VITE_REPLAY_SAMPLE_RATE（默认 0.01=1%），缓冲上限 500，定时 60s
 * - 模型配置：GLM-5.2
 * - 关键状态：动态 import rrweb；pako.gzip+base64 压缩；sendBeacon 优先、fetch 回退+重试；AbortController 竞态防御
 *
 * 状态同步机制说明（注释）：
 * - AbortController：每次上传创建独立控制器，stopRecording 时 abort 未完成请求
 * - Request ID：replay_id 使用 crypto.randomUUID() 保证幂等
 * - Optimistic Rollback：上传失败批次写入 localStorage 'yunshu_replay_failed_uploads'（最多 10 条）供下次重试
 * - 防抖节流：60s 定时 flush + 缓冲满 500 触发 flush；isFlushing 标志防止并发
 */
import pako from 'pako'

// ════════════════════════════════════════════════════════════════
//  常量与配置
// ════════════════════════════════════════════════════════════════

const MODULE_NAME = 'session_replay' as const

/** 缓冲区最大事件数（避免内存爆炸） */
const MAX_BUFFER_SIZE = 500

/** 定时 flush 间隔（毫秒） */
const FLUSH_INTERVAL_MS = 60_000

/** 上传失败重试次数 */
const MAX_UPLOAD_RETRIES = 3

/** 失败上传本地存储键（最多保留 10 条） */
const FAILED_UPLOADS_KEY = 'yunshu_replay_failed_uploads'

/** trace_id 本地存储键 */
const TRACE_ID_KEY = 'yunshu_trace_id'

/** 用户会话 ID 本地存储键 */
const SESSION_ID_KEY = 'yunshu_session_id'

/** 缓冲事件类型：rrweb 事件 + _ts 时间戳（使用 Record 而非 any） */
type BufferedEvent = Record<string, unknown> & { _ts: number }

/** 上传请求体类型 */
interface UploadBody {
  replay_id: string
  trace_id?: string
  user_session_id?: string
  timestamp: string
  duration_sec: number
  event_count: number
  compressed: boolean
  encoding: 'gzip-base64' | 'json'
  data: string
}

/** flush 选项 */
interface FlushOptions {
  /** true=页面卸载场景，优先 sendBeacon（fire-and-forget，不重试） */
  beacon?: boolean
}

// ════════════════════════════════════════════════════════════════
//  模块级状态
// ════════════════════════════════════════════════════════════════

/** 录制事件缓冲 */
const buffer: BufferedEvent[] = []

/** rrweb stop 函数 */
let stopFn: (() => void) | null = null

/** 定时 flush 句柄 */
let flushTimer: ReturnType<typeof setInterval> | null = null

/** 当前上传 AbortController（用于竞态取消） */
let currentUploadController: AbortController | null = null

/** flush 并发标志（防抖） */
let isFlushing = false

/** 采样启用缓存（会话内一次性判断，不变） */
let recordingEnabledCache: boolean | null = null

// ════════════════════════════════════════════════════════════════
//  通用工具函数
// ════════════════════════════════════════════════════════════════

/** 读取 localStorage 字符串（容错） */
function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** 读取 trace_id（优先 localStorage） */
function readTraceId(): string | null {
  return readStorage(TRACE_ID_KEY)
}

/** 读取用户会话 ID */
function readSessionId(): string | null {
  return readStorage(SESSION_ID_KEY)
}

/** 解析采样率字符串为 [0,1] 数值，非法则回退默认 */
function parseSampleRate(raw: string | undefined, fallback: number): number {
  if (raw == null || raw === '') return fallback
  const n = Number(raw)
  if (!Number.isFinite(n) || n < 0 || n > 1) return fallback
  return n
}

/** 生成回放唯一 ID（crypto.randomUUID 优先，回退时间戳+随机） */
function generateReplayId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
  } catch {
    // 回退到下面的兜底方案
  }
  return `rpl-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/** sleep 工具 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** Uint8Array → base64（分块避免 fromCharCode 栈溢出） */
function uint8ToBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    const slice = bytes.subarray(i, i + chunk)
    binary += String.fromCharCode(...slice)
  }
  return btoa(binary)
}

/** 计算事件序列时长（秒）：首末 timestamp 之差 */
function computeDurationSec(events: BufferedEvent[]): number {
  const timestamps: number[] = []
  for (const e of events) {
    const ts = e.timestamp
    if (typeof ts === 'number' && ts > 0) timestamps.push(ts)
  }
  if (timestamps.length < 2) return 0
  const max = Math.max(...timestamps)
  const min = Math.min(...timestamps)
  return Math.max(0, Math.round((max - min) / 1000))
}

/** 获取上传端点 */
function getUploadUrl(): string {
  return import.meta.env.VITE_REPLAY_UPLOAD_URL ?? '/api/replay/upload'
}

/** 类型守卫：判断是否为普通对象 */
function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

// ════════════════════════════════════════════════════════════════
//  结构化日志（JSON 格式，含 trace_id/module_name/action/duration_ms）
// ════════════════════════════════════════════════════════════════

interface LogPayload {
  [key: string]: unknown
}

function logJson(
  level: 'log' | 'warn' | 'error',
  action: string,
  payload: LogPayload,
  start?: number,
): void {
  const entry = {
    trace_id: readTraceId() ?? null,
    module_name: MODULE_NAME,
    action,
    duration_ms: start != null ? Date.now() - start : 0,
    ...payload,
  }
  // eslint-disable-next-line no-console
  console[level](JSON.stringify(entry))
}

// ════════════════════════════════════════════════════════════════
//  压缩与上传
// ════════════════════════════════════════════════════════════════

/** 将事件数组 gzip 压缩后 base64 编码 */
function compressEvents(events: BufferedEvent[]): string {
  const json = JSON.stringify(events)
  const bytes = new TextEncoder().encode(json)
  // pako.gzip 返回 Uint8Array
  const compressed = pako.gzip(bytes) as Uint8Array
  return uint8ToBase64(compressed)
}

/** 构造上传请求体 */
function buildUploadBody(events: BufferedEvent[]): UploadBody {
  const replayId = generateReplayId()
  const traceId = readTraceId() ?? undefined
  const sessionId = readSessionId() ?? undefined
  let data: string
  try {
    data = compressEvents(events)
  } catch (e) {
    // 边界显性化：压缩失败抛出明确错误
    throw new Error(`[session_replay] 压缩失败: ${(e as Error).message}`)
  }
  return {
    replay_id: replayId,
    trace_id: traceId,
    user_session_id: sessionId,
    timestamp: new Date().toISOString(),
    duration_sec: computeDurationSec(events),
    // 注意：后端 routes_replay.py 字段为 event_count（非 events_count）
    event_count: events.length,
    compressed: true,
    encoding: 'gzip-base64',
    data,
  }
}

/** sendBeacon 单次发送（页面卸载可用，fire-and-forget） */
function sendBeaconOnce(body: UploadBody): boolean {
  try {
    if (typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') {
      return false
    }
    const blob = new Blob([JSON.stringify(body)], { type: 'application/json' })
    return navigator.sendBeacon(getUploadUrl(), blob)
  } catch {
    return false
  }
}

/** fetch 单次发送（带 AbortController，可取消） */
async function sendFetchOnce(
  body: UploadBody,
  signal: AbortSignal,
  keepalive = false,
): Promise<boolean> {
  const res = await fetch(getUploadUrl(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    keepalive,
    signal,
  })
  if (!res.ok) {
    // 边界显性化：HTTP 非 2xx 抛出带状态码的错误
    throw new Error(`[session_replay] 上传失败 HTTP ${res.status}`)
  }
  return true
}

/** 记录失败上传到 localStorage（最多保留 10 条，供下次重试） */
function recordFailedUpload(body: UploadBody): void {
  try {
    const existing = readStorage(FAILED_UPLOADS_KEY)
    const list: unknown = existing ? JSON.parse(existing) : []
    const arr = Array.isArray(list) ? list : []
    arr.push({ ...body, _failed_at: new Date().toISOString() })
    // 仅保留最近 10 条
    const trimmed = arr.slice(-10)
    localStorage.setItem(FAILED_UPLOADS_KEY, JSON.stringify(trimmed))
  } catch {
    // 存储不可用（如隐私模式），忽略
  }
}

/**
 * 上传录制数据。
 * - beacon 模式（页面卸载）：优先 sendBeacon，失败回退 fetch(keepalive)，不重试
 * - 默认模式（定时/缓冲满）：fetch + AbortController，最多 3 次指数退避（1s/2s/4s）
 */
async function uploadReplay(
  events: BufferedEvent[],
  opts: FlushOptions = {},
): Promise<boolean> {
  const start = Date.now()
  if (events.length === 0) return true

  let body: UploadBody
  try {
    body = buildUploadBody(events)
  } catch (e) {
    logJson('error', 'build_body_failed', { error: (e as Error).message }, start)
    throw e
  }

  // —— beacon 模式：卸载场景，单次发送不重试 ——
  if (opts.beacon) {
    const ok = sendBeaconOnce(body)
    if (ok) {
      logJson('log', 'upload_beacon_success', { event_count: events.length, bytes: body.data.length }, start)
      return true
    }
    // sendBeacon 失败/不可用 → 回退 fetch(keepalive)
    try {
      const controller = new AbortController()
      currentUploadController = controller
      await sendFetchOnce(body, controller.signal, true)
      currentUploadController = null
      logJson('log', 'upload_beacon_fallback_success', { event_count: events.length }, start)
      return true
    } catch (e) {
      currentUploadController = null
      recordFailedUpload(body)
      logJson('warn', 'upload_beacon_failed', { error: (e as Error).message }, start)
      return false
    }
  }

  // —— 默认模式：fetch + 重试 ——
  const controller = new AbortController()
  currentUploadController = controller
  let lastError: Error | null = null
  try {
    for (let attempt = 1; attempt <= MAX_UPLOAD_RETRIES; attempt++) {
      try {
        await sendFetchOnce(body, controller.signal, false)
        logJson('log', 'upload_success', { attempt, event_count: events.length, bytes: body.data.length }, start)
        return true
      } catch (e) {
        // AbortError 表示被主动取消，无需重试
        if (controller.signal.aborted) {
          logJson('warn', 'upload_aborted', { attempt }, start)
          return false
        }
        lastError = e as Error
        logJson('warn', 'upload_attempt_failed', { attempt, error: lastError.message }, start)
        if (attempt < MAX_UPLOAD_RETRIES) {
          // 指数退避：1s, 2s, 4s
          await sleep(Math.pow(2, attempt - 1) * 1000)
        }
      }
    }
    // 全部重试失败：记录到 localStorage 供下次重试
    recordFailedUpload(body)
    throw new Error(`[session_replay] 上传失败（已重试 ${MAX_UPLOAD_RETRIES} 次）: ${lastError?.message ?? 'unknown'}`)
  } finally {
    currentUploadController = null
  }
}

// ════════════════════════════════════════════════════════════════
//  缓冲区管理
// ════════════════════════════════════════════════════════════════

/** rrweb emit 回调：写入缓冲，满则触发 flush */
function pushEvent(event: unknown): void {
  try {
    if (!isRecord(event)) return
    const enriched: BufferedEvent = { ...event, _ts: Date.now() } as BufferedEvent
    buffer.push(enriched)
    if (buffer.length >= MAX_BUFFER_SIZE) {
      // 缓冲满，触发上传（防抖：isFlushing 控制并发）
      void flushReplay()
    }
  } catch (e) {
    // 录制异常：记录并自动停止，避免循环报错
    logJson('error', 'push_event_failed', { error: (e as Error).message }, Date.now())
    void stopRecording().catch(() => {})
  }
}

// ════════════════════════════════════════════════════════════════
//  导出 API
// ════════════════════════════════════════════════════════════════

/**
 * 判断当前会话是否启用录制（一次性判断，会话内不变）。
 * - 开发环境（import.meta.env.DEV）默认不启用
 * - 生产环境按 VITE_REPLAY_SAMPLE_RATE 采样（默认 0.01 = 1%）
 */
export function isRecordingEnabled(): boolean {
  if (recordingEnabledCache !== null) return recordingEnabledCache
  // 开发环境默认不启用
  if (import.meta.env.DEV) {
    recordingEnabledCache = false
    return false
  }
  const rate = parseSampleRate(import.meta.env.VITE_REPLAY_SAMPLE_RATE, 0.01)
  recordingEnabledCache = Math.random() < rate
  logJson('log', 'sample_decision', { sample_rate: rate, enabled: recordingEnabledCache }, 0)
  return recordingEnabledCache
}

/**
 * 启动 rrweb 录制。
 * - 动态 import rrweb 避免打包膨胀
 * - 设置 60s 定时 flush
 */
export async function startRecording(): Promise<void> {
  const start = Date.now()
  if (stopFn) {
    logJson('log', 'already_recording', {}, start)
    return
  }
  try {
    // 动态 import rrweb，避免主包体积膨胀
    const rrweb = await import('rrweb')
    const record = rrweb.record
    if (typeof record !== 'function') {
      throw new Error('rrweb.record 不可用')
    }
    // 调用 record，返回 stop 函数
    const ret: unknown = record({
      emit(event: unknown) {
        pushEvent(event)
      },
    })
    if (typeof ret === 'function') {
      stopFn = ret as () => void
    }
    // 定时 flush（防抖：60s 节流上传）
    flushTimer = setInterval(() => {
      void flushReplay()
    }, FLUSH_INTERVAL_MS)
    logJson('log', 'start_recording', { flush_interval_ms: FLUSH_INTERVAL_MS, max_buffer: MAX_BUFFER_SIZE }, start)
  } catch (e) {
    logJson('error', 'start_recording_failed', { error: (e as Error).message }, start)
    // 边界显性化：启动失败抛出明确错误
    throw new Error(`[session_replay] 启动录制失败: ${(e as Error).message}`)
  }
}

/**
 * 停止录制。
 * - 调用 rrweb stop
 * - 清理定时器
 * - abort 未完成的上传请求（竞态防御）
 */
export async function stopRecording(): Promise<void> {
  const start = Date.now()
  try {
    if (typeof stopFn === 'function') {
      stopFn()
    }
  } catch (e) {
    logJson('warn', 'stop_fn_error', { error: (e as Error).message }, start)
  }
  stopFn = null
  if (flushTimer) {
    clearInterval(flushTimer)
    flushTimer = null
  }
  // 取消未完成的上传请求
  if (currentUploadController) {
    try {
      currentUploadController.abort()
    } catch {
      // ignore
    }
    currentUploadController = null
  }
  logJson('log', 'stop_recording', {}, start)
}

/**
 * 刷新缓冲区：将已录制事件上传。
 * - 防抖：isFlushing 标志防止并发 flush
 * - beacon=true 时优先 sendBeacon（页面卸载场景）
 */
export async function flushReplay(opts: FlushOptions = {}): Promise<void> {
  const start = Date.now()
  if (isFlushing) {
    logJson('log', 'flush_skipped_concurrent', {}, start)
    return
  }
  if (buffer.length === 0) return
  isFlushing = true
  // 取出全部事件（splice 清空缓冲，新事件进入空缓冲）
  const batch = buffer.splice(0, buffer.length)
  try {
    await uploadReplay(batch, opts)
  } catch (e) {
    // 上传失败已记录到 failed_uploads，不重新放回缓冲避免无限增长
    logJson('warn', 'flush_failed', { error: (e as Error).message, event_count: batch.length }, start)
  } finally {
    isFlushing = false
  }
}
