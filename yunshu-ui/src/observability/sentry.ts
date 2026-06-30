/**
 * 【生成日志摘要】
 * - 生成时间：2026-06-28
 * - 内容：Sentry 前端 SDK 初始化模块（v1）
 * - 参数：DSN/环境/采样率均由 VITE_SENTRY_* 环境变量读取
 * - 模型配置：GLM-5.2
 * - 关键状态：browserTracingIntegration；全局 error/unhandledrejection 转发；trace_id 从 Sentry hub 获取
 *
 * 状态同步机制说明（注释）：
 * - 全局错误处理器：window.addEventListener('error'/'unhandledrejection') 转发到 Sentry（Sentry 默认 Dedupe 集成会去重，避免重复上报）
 * - 失败不阻塞：DSN 未配置时 console.warn 后返回 false，主流程继续
 */
import * as Sentry from '@sentry/react'

// ════════════════════════════════════════════════════════════════
//  常量与配置
// ════════════════════════════════════════════════════════════════

const MODULE_NAME = 'sentry' as const

/** 应用版本号（用于 Sentry release 字段） */
const APP_VERSION: string = import.meta.env.VITE_APP_VERSION ?? '0.0.0'

/** trace_id 本地存储键（Sentry hub 不可用时的回退来源） */
const TRACE_ID_KEY = 'yunshu_trace_id'

// ════════════════════════════════════════════════════════════════
//  通用工具函数
// ════════════════════════════════════════════════════════════════

/** 解析采样率字符串为 [0,1] 数值，非法则回退默认 */
function parseRate(raw: string | undefined, fallback: number): number {
  if (raw == null || raw === '') return fallback
  const n = Number(raw)
  if (!Number.isFinite(n) || n < 0 || n > 1) return fallback
  return n
}

/**
 * 校验 DSN 格式（Sentry DSN 形如 https://<key>@<host>/<project>）。
 * 使用 URL 解析校验协议头，避免误判。
 */
function isValidDsn(dsn: string): boolean {
  if (typeof dsn !== 'string' || dsn.length === 0) return false
  try {
    const url = new URL(dsn)
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname.length > 0
  } catch {
    return false
  }
}

/** 读取 localStorage 字符串（容错） */
function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** 错误对象转字符串 */
function errorToString(err: unknown): string {
  if (err instanceof Error) return `${err.name}: ${err.message}`
  return String(err)
}

/**
 * 从 Sentry 当前 hub 获取 trace_id（不同版本 API 差异，使用受控类型断言，非 any）。
 * 失败回退到 localStorage 的 'yunshu_trace_id'。
 */
function getTraceId(): string | null {
  // 优先从 Sentry hub 获取
  try {
    const hub = Sentry.getCurrentHub() as unknown as {
      getScope?: () => {
        getTransaction?: () => { traceId?: string } | undefined
      } | undefined
    }
    const tx = hub.getScope?.()?.getTransaction?.()
    if (tx?.traceId) return tx.traceId
  } catch {
    // hub 不可用，回退
  }
  // 回退到 localStorage
  return readStorage(TRACE_ID_KEY)
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
    trace_id: getTraceId() ?? null,
    module_name: MODULE_NAME,
    action,
    duration_ms: start != null ? Date.now() - start : 0,
    ...payload,
  }
  // eslint-disable-next-line no-console
  console[level](JSON.stringify(entry))
}

// ════════════════════════════════════════════════════════════════
//  全局错误处理器（转发到 Sentry）
// ════════════════════════════════════════════════════════════════

/**
 * 安装全局错误处理器（window.onerror / window.onunhandledrejection 的现代等价形式）。
 * 使用 addEventListener 非破坏式注册；Sentry 默认 Dedupe 集成会自动去重相同异常。
 */
function installGlobalErrorHandlers(): void {
  // 同步错误
  window.addEventListener('error', (event) => {
    try {
      const err = event.error ?? new Error(event.message ?? 'unknown error')
      Sentry.captureException(err)
    } catch {
      // 转发失败不阻塞
    }
  })
  // Promise 未处理拒绝
  window.addEventListener('unhandledrejection', (event) => {
    try {
      Sentry.captureException(event.reason)
    } catch {
      // 转发失败不阻塞
    }
  })
}

// ════════════════════════════════════════════════════════════════
//  导出 API
// ════════════════════════════════════════════════════════════════

/**
 * 初始化 Sentry SDK。
 * - DSN 未配置：console.warn 后返回 false，不阻塞应用
 * - DSN 格式非法：抛 Error（边界显性化）
 * - 初始化成功：安装全局错误处理器，返回 true
 */
export function initSentry(): boolean {
  const start = Date.now()

  const dsn = import.meta.env.VITE_SENTRY_DSN ?? ''
  const environment = import.meta.env.VITE_SENTRY_ENVIRONMENT ?? 'development'
  const tracesSampleRate = parseRate(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE, 0.01)
  const replaysSessionSampleRate = parseRate(import.meta.env.VITE_SENTRY_REPLAYS_SAMPLE_RATE, 0.01)

  // DSN 未配置：警告后返回 false，不阻塞主流程
  if (!dsn) {
    logJson('warn', 'init_skipped_no_dsn', { reason: 'VITE_SENTRY_DSN 为空' }, start)
    return false
  }

  // 边界显性化：DSN 格式校验失败抛出明确错误
  if (!isValidDsn(dsn)) {
    const err = new Error(`[sentry] DSN 格式无效: ${dsn}`)
    logJson('error', 'init_dsn_invalid', { dsn }, start)
    throw err
  }

  try {
    Sentry.init({
      dsn,
      environment,
      // browserTracingIntegration 在 v8 为函数调用形式（注意带 ()）
      integrations: [Sentry.browserTracingIntegration()],
      tracesSampleRate,
      replaysSessionSampleRate,
      // 发生错误时 100% 上传回放（由 Sentry replay 集成消费；rrweb 为独立回放通道）
      replaysOnErrorSampleRate: 1.0,
      release: APP_VERSION,
    })

    // 安装全局错误处理器转发到 Sentry
    installGlobalErrorHandlers()

    logJson('log', 'init_success', { environment, release: APP_VERSION, tracesSampleRate, replaysSessionSampleRate }, start)
    return true
  } catch (e) {
    logJson('error', 'init_failed', { error: (e as Error).message }, start)
    // 边界显性化：初始化失败抛出明确错误
    throw new Error(`[sentry] 初始化失败: ${(e as Error).message}`)
  }
}

/**
 * 捕获错误并上报到 Sentry（添加 breadcrumb 上下文）。
 * - 包装 Sentry.captureException，附加 extra 上下文
 * - 失败不抛出，仅记录日志
 */
export function captureError(error: unknown, context?: Record<string, unknown>): void {
  const start = Date.now()
  try {
    // 添加面包屑，便于在 Sentry 事件中追踪上下文
    Sentry.addBreadcrumb({
      category: 'capture_error',
      level: 'error',
      data: context ?? {},
      timestamp: Date.now() / 1000,
    })
    Sentry.captureException(error, { extra: context })
    logJson('log', 'capture_error', { error: errorToString(error) }, start)
  } catch (e) {
    // 捕获失败不抛出，避免影响调用方
    logJson('error', 'capture_error_failed', { error: (e as Error).message }, start)
  }
}
