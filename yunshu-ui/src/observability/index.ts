/**
 * 【生成日志摘要】
 * - 生成时间：2026-06-28
 * - 内容：可观测性统一入口（v1）
 * - 参数：无
 * - 模型配置：GLM-5.2
 * - 关键状态：initObservability 串行初始化 Sentry + rrweb；注册 beforeunload/visibilitychange flush
 *
 * 状态同步机制说明（注释）：
 * - 失败不阻塞：Sentry/rrweb 任一初始化失败均捕获并记录，主流程继续
 * - 生命周期 flush：beforeunload 与 visibilitychange(hidden) 时以 beacon 模式 flush，确保卸载期间数据不丢
 */
import { initSentry, captureError } from './sentry'
import { startRecording, stopRecording, flushReplay, isRecordingEnabled } from './sessionReplay'

export { initSentry, captureError, startRecording, stopRecording, flushReplay, isRecordingEnabled }

/** 初始化结果 */
export interface ObservabilityInitResult {
  sentryEnabled: boolean
  replayEnabled: boolean
}

const MODULE_NAME = 'observability' as const

function logJson(action: string, payload: Record<string, unknown>, durationMs: number): void {
  const entry = {
    module_name: MODULE_NAME,
    action,
    duration_ms: durationMs,
    ...payload,
  }
  // eslint-disable-next-line no-console
  console.log(JSON.stringify(entry))
}

/**
 * 初始化可观测性（Sentry 错误上报 + rrweb 行为回放）。
 * - 依次调用 initSentry()，成功且 isRecordingEnabled() 则 startRecording()
 * - 失败不阻塞主流程
 * - 注册 beforeunload / visibilitychange(hidden) 触发 flushReplay(beacon)
 *
 * @returns { sentryEnabled, replayEnabled }
 */
export function initObservability(): ObservabilityInitResult {
  const start = Date.now()
  const result: ObservabilityInitResult = { sentryEnabled: false, replayEnabled: false }

  // 1. Sentry 初始化（失败不阻塞）
  try {
    result.sentryEnabled = initSentry()
  } catch (e) {
    logJson('sentry_init_error', { error: (e as Error).message }, Date.now() - start)
  }

  // 2. rrweb 录制：仅当 Sentry 启用且采样命中时启动
  try {
    if (result.sentryEnabled && isRecordingEnabled()) {
      // startRecording 异步动态 import rrweb，不阻塞主流程
      void startRecording()
      result.replayEnabled = true
    }
  } catch (e) {
    logJson('replay_start_error', { error: (e as Error).message }, Date.now() - start)
  }

  // 3. 生命周期事件：页面卸载 / 切到后台时 flush（beacon 模式，确保卸载期间可发送）
  try {
    window.addEventListener('beforeunload', () => {
      void flushReplay({ beacon: true })
    })
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        void flushReplay({ beacon: true })
      }
    })
  } catch (e) {
    // 事件注册失败不阻塞
    logJson('lifecycle_hook_error', { error: (e as Error).message }, 0)
  }

  logJson('init_done', { ...result }, Date.now() - start)
  return result
}
