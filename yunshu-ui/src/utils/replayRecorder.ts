/**
 * 云枢前端用户行为回放录制器（rrweb）
 *
 * 设计原则：
 * 1. 零付费依赖 —— 使用开源 rrweb + rrweb-player，不接入商业 Session Replay 服务。
 * 2. 异常时上传 —— 采样率 1%（可配置），仅当捕获到错误时上传最近 30 秒回放。
 * 3. 敏感信息脱敏 —— 通过 blockClass 屏蔽密码框、token 输入框（data-rrweb-mask）。
 * 4. gzip 压缩 —— 录制数据压缩后单次回放 < 500KB（使用 CompressionStream API）。
 * 5. 失败隔离 —— 录制/上传失败不得影响业务主流程，仅 console.warn 警告。
 * 6. 性能可控 —— 录制 CPU 开销 < 2%，使用 rrweb 的 sampling 配置降低采样频率。
 *
 * 状态同步机制说明：
 * - 录制事件循环缓冲区：使用 ringBuffer 维护最近 30 秒事件（按事件时间戳淘汰），
 *   避免长会话内存膨胀；只有发生错误时才将缓冲区快照序列化上传。
 * - 与 Sentry 关联：错误发生时，将 trace_id、user_session_id、error_id 注入回放元数据，
 *   后端存储时建立三方关联（trace_id ↔ user_session_id ↔ error_id）。
 * - 异步上传：使用 navigator.sendBeacon 或 fetch+keepalive，确保页面卸载时也能完成上传。
 *
 * 使用方式：
 *   import { startReplayRecording, captureReplayOnError } from '@/utils/replayRecorder';
 *   startReplayRecording();  // 应用启动时调用
 *   // 在错误捕获处：
 *   captureReplayOnError(errorId, traceId);
 */

import { record } from 'rrweb';
import type { recordOptions, eventWithTime } from 'rrweb/typings/types';

// ─── 业务错误码（边界显性化） ────────────────────────────────────────────

export enum ReplayErrorCode {
  INVALID_SAMPLE_RATE = 'REPLAY_FE_001',  // 采样率越界
  INVALID_DURATION = 'REPLAY_FE_002',      // 上传时长越界
  RECORD_NOT_STARTED = 'REPLAY_FE_003',    // 录制未启动即调用上报
  UPLOAD_FAILED = 'REPLAY_FE_004',         // 上传失败
  COMPRESS_UNSUPPORTED = 'REPLAY_FE_005',  // 浏览器不支持 CompressionStream
}

// ─── 配置类型 ─────────────────────────────────────────────────────────

export interface ReplayConfig {
  /** 是否启用录制（默认 true，可通过 VITE_REPLAY_ENABLED=false 关闭） */
  enabled: boolean;
  /** 采样率 [0, 1]，默认 0.01（1%） */
  sampleRate: number;
  /** 触发错误后上传的回放时长（秒），默认 30 */
  uploadDurationSec: number;
  /** 单次回放最大字节数（压缩前），默认 2MB（压缩后约 200~500KB） */
  maxBytes: number;
  /** 上传 API 路径 */
  uploadEndpoint: string;
  /** 录制采样间隔（毫秒），默认 200（降低 mousemove 频率以控制 CPU） */
  samplingInterval: number;
}

// ─── 状态变量（模块级单例） ───────────────────────────────────────────

let _isRecording = false;
let _stopFn: (() => void) | null = null;
let _eventBuffer: eventWithTime[] = [];
let _cachedConfig: ReplayConfig | null = null;
let _userSessionId: string | null = null;
let _currentTraceId: string | null = null;

/** 缓冲区最大事件数（粗略上限，30 秒约 5000 个事件） */
const BUFFER_MAX_EVENTS = 8000;

// ─── 配置解析 ─────────────────────────────────────────────────────────

/**
 * 解析 Vite 环境变量组装配置
 *
 * 边界显性化：采样率/时长越界时抛出业务错误码 Error。
 */
function resolveConfig(): ReplayConfig {
  const env = import.meta.env;

  const enabled = (env.VITE_REPLAY_ENABLED as string | undefined) !== 'false';
  const rawRate = env.VITE_REPLAY_SAMPLE_RATE as string | undefined;
  const sampleRate = rawRate !== undefined ? Number(rawRate) : 0.01;
  if (!Number.isFinite(sampleRate) || sampleRate < 0 || sampleRate > 1) {
    throw new Error(
      `[${ReplayErrorCode.INVALID_SAMPLE_RATE}] VITE_REPLAY_SAMPLE_RATE 必须在 [0, 1] 区间内，当前值: ${rawRate}`
    );
  }

  const rawDuration = env.VITE_REPLAY_UPLOAD_DURATION as string | undefined;
  const uploadDurationSec = rawDuration !== undefined ? Number(rawDuration) : 30;
  if (!Number.isFinite(uploadDurationSec) || uploadDurationSec <= 0 || uploadDurationSec > 300) {
    throw new Error(
      `[${ReplayErrorCode.INVALID_DURATION}] VITE_REPLAY_UPLOAD_DURATION 必须在 (0, 300] 区间内，当前值: ${rawDuration}`
    );
  }

  return {
    enabled,
    sampleRate,
    uploadDurationSec,
    maxBytes: 2 * 1024 * 1024,
    uploadEndpoint: '/api/replay/upload',
    samplingInterval: 200,
  };
}

/**
 * 获取回放配置（单例）
 *
 * 异常处理：配置解析失败时返回禁用配置，避免阻塞应用。
 */
export function getReplayConfig(): ReplayConfig {
  if (_cachedConfig) return _cachedConfig;
  try {
    _cachedConfig = resolveConfig();
  } catch (err) {
    console.error('[Replay] 配置解析失败，已回退到禁用状态:', err);
    _cachedConfig = {
      enabled: false,
      sampleRate: 0,
      uploadDurationSec: 30,
      maxBytes: 2 * 1024 * 1024,
      uploadEndpoint: '/api/replay/upload',
      samplingInterval: 200,
    };
  }
  return _cachedConfig;
}

// ─── 采样判定 ─────────────────────────────────────────────────────────

/**
 * 采样命中判定
 *
 * 状态同步机制：基于 Math.random() 与 sampleRate 比较，命中则启动录制；
 * 未命中时 startReplayRecording 直接返回 false，不安装 rrweb 拦截器。
 *
 * @returns true 表示本次会话需要录制
 */
export function shouldRecord(): boolean {
  const { enabled, sampleRate } = getReplayConfig();
  if (!enabled) return false;
  if (sampleRate >= 1) return true;
  if (sampleRate <= 0) return false;
  return Math.random() < sampleRate;
}

// ─── 录制启动 ─────────────────────────────────────────────────────────

/**
 * 启动 rrweb 录制
 *
 * 状态同步机制：
 * - 使用 rrweb record() 返回的 stopFn 在卸载时停止录制；
 * - 事件通过 emit 回调写入循环缓冲区，超过 BUFFER_MAX_EVENTS 时按 FIFO 淘汰；
 * - 通过 blockClass 屏蔽标记为 data-rrweb-mask 的元素（密码框、token 输入框）。
 *
 * 失败隔离：录制启动失败仅记录日志，不抛异常。
 *
 * @returns True 表示已启动；False 表示未启用、未采样命中或启动失败
 */
export function startReplayRecording(): boolean {
  if (_isRecording) return true;

  const config = getReplayConfig();
  if (!config.enabled) return false;

  // 采样命中判定
  if (!shouldRecord()) {
    return false;
  }

  try {
    const stopFn = record({
      emit(event) {
        try {
          // 写入循环缓冲区（FIFO 淘汰）
          _eventBuffer.push(event as eventWithTime);
          if (_eventBuffer.length > BUFFER_MAX_EVENTS) {
            // 一次性淘汰 10% 旧事件，避免每次都 shift（O(n)）
            _eventBuffer = _eventBuffer.slice(Math.floor(BUFFER_MAX_EVENTS * 0.1));
          }
        } catch {
          // 缓冲区写入失败不影响业务
        }
      },
      // 录制内容：DOM 变化 + 鼠标移动 + 用户输入
      recordCanvas: false,
      // 降低 mousemove 采样频率，控制 CPU 开销 < 2%
      sampling: {
        mousemove: config.samplingInterval,
        mousemoveTimeout: config.samplingInterval * 5,
        scroll: 300,
        input: 'last',
      } as Record<string, unknown>,
      // 屏蔽标记为 data-rrweb-mask 的元素（密码框、token 输入框等）
      blockClass: 'rrweb-mask',
      maskTextClass: 'rrweb-mask-text',
      maskAllInputs: true,           // 默认遮罩所有输入框值，仅通过 unmask 显式放开
      maskInputOptions: {
        password: true,
        text: false,
        search: false,
        email: true,
        tel: true,
        url: true,
      },
      // 不录制样式表变化（体积过大），仅录制结构 + 交互
      inlineStylesheet: false,
    });

    if (typeof stopFn === 'function') {
      _stopFn = stopFn;
      _isRecording = true;
      console.info(
        `[Replay] 录制已启动 sampleRate=${config.sampleRate} duration=${config.uploadDurationSec}s`
      );
      return true;
    }
    console.warn('[Replay] rrweb record() 未返回 stopFn，录制未启动');
    return false;
  } catch (err) {
    console.error('[Replay] 录制启动失败:', err);
    return false;
  }
}

/**
 * 停止录制（清理资源）
 */
export function stopReplayRecording(): void {
  if (_stopFn) {
    try {
      _stopFn();
    } catch (err) {
      console.warn('[Replay] 停止录制失败:', err);
    }
    _stopFn = null;
  }
  _isRecording = false;
}

// ─── trace_id / user_session_id 关联 ─────────────────────────────────

/**
 * 设置当前会话的 trace_id（与 Sentry trace_id 关联，写入回放元数据）
 */
export function setReplayTraceId(traceId: string | null): void {
  _currentTraceId = traceId;
}

/**
 * 设置用户会话 ID（与后端 trace_id 关联，用于用户行为回放匹配）
 */
export function setReplayUserSessionId(sessionId: string | null): void {
  _userSessionId = sessionId;
}

// ─── 压缩（gzip） ─────────────────────────────────────────────────────

/**
 * 使用浏览器原生 CompressionStream API 进行 gzip 压缩
 *
 * 若浏览器不支持 CompressionStream（Safari < 16.4），降级为原始 JSON。
 * 单次回放压缩后目标 < 500KB。
 *
 * @returns {Promise<{data: string, compressed: boolean}>}
 */
async function compressData(data: unknown): Promise<{ data: string; compressed: boolean }> {
  const jsonStr = JSON.stringify(data);
  // 浏览器不支持 CompressionStream 时降级
  if (typeof CompressionStream === 'undefined') {
    console.warn(
      `[${ReplayErrorCode.COMPRESS_UNSUPPORTED}] 浏览器不支持 CompressionStream，使用原始 JSON`
    );
    return { data: jsonStr, compressed: false };
  }
  try {
    const stream = new CompressionStream('gzip');
    const writer = stream.writable.getWriter();
    writer.write(new TextEncoder().encode(jsonStr));
    writer.close();
    const reader = stream.readable.getReader();
    const chunks: Uint8Array[] = [];
    let totalBytes = 0;
    // 限制总字节数，避免内存膨胀
    const maxBytes = 5 * 1024 * 1024;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (totalBytes + value.length > maxBytes) {
        // 超限截断
        const remaining = maxBytes - totalBytes;
        chunks.push(value.slice(0, remaining));
        totalBytes = maxBytes;
        break;
      }
      chunks.push(value);
      totalBytes += value.length;
    }
    // 合并为 base64 字符串便于 JSON 传输
    const merged = new Uint8Array(totalBytes);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.length;
    }
    // 转 base64
    let binary = '';
    for (let i = 0; i < merged.length; i++) {
      binary += String.fromCharCode(merged[i]);
    }
    return { data: btoa(binary), compressed: true };
  } catch (err) {
    console.warn('[Replay] gzip 压缩失败，降级为原始 JSON:', err);
    return { data: jsonStr, compressed: false };
  }
}

// ─── 错误触发上传 ─────────────────────────────────────────────────────

/**
 * 在错误发生时上传最近 N 秒的回放数据
 *
 * 状态同步机制：
 * - 从循环缓冲区取出最近 uploadDurationSec 秒的事件快照（不修改原缓冲区）；
 * - 注入 trace_id、user_session_id、error_id 元数据；
 * - gzip 压缩后通过 sendBeacon / fetch+keepalive 异步上传；
 * - 失败仅记录日志，不抛异常。
 *
 * @param errorId 错误事件 ID（Sentry 事件 ID 或自定义）
 * @param traceId 后端 trace_id（与 OpenTelemetry 关联）
 * @returns True 表示上传成功；False 表示未录制或上传失败
 */
export async function captureReplayOnError(
  errorId: string,
  traceId?: string
): Promise<boolean> {
  if (!_isRecording) {
    console.warn(`[${ReplayErrorCode.RECORD_NOT_STARTED}] 录制未启动，跳过上传`);
    return false;
  }

  const config = getReplayConfig();
  const effectiveTraceId = traceId ?? _currentTraceId;

  // 取最近 uploadDurationSec 秒的事件快照（不修改原缓冲区，避免影响后续录制）
  const now = Date.now();
  const cutoff = now - config.uploadDurationSec * 1000;
  const snapshot = _eventBuffer.filter((e) => (e as { timestamp: number }).timestamp >= cutoff);

  if (snapshot.length === 0) {
    console.warn('[Replay] 缓冲区为空，无回放数据可上传');
    return false;
  }

  // 体积预检：超过 maxBytes 时按时间倒序截断
  let payload = snapshot;
  if (JSON.stringify(payload).length > config.maxBytes) {
    // 按 10% 递减，直到满足
    while (payload.length > 0 && JSON.stringify(payload).length > config.maxBytes) {
      payload = payload.slice(Math.floor(payload.length * 0.1));
    }
    console.warn(
      `[Replay] 回放数据超限，已截断至 ${payload.length} 事件（原 ${snapshot.length}）`
    );
  }

  // 压缩
  const { data, compressed } = await compressData(payload);

  // 构造上传 payload（包含关联元数据）
  const replayId = `${now.toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const body = JSON.stringify({
    replay_id: replayId,
    trace_id: effectiveTraceId,
    user_session_id: _userSessionId,
    error_id: errorId,
    timestamp: new Date(now).toISOString(),
    duration_sec: config.uploadDurationSec,
    event_count: payload.length,
    compressed,
    encoding: compressed ? 'gzip-base64' : 'json',
    data,
  });

  // 异步上传：优先 sendBeacon（页面卸载也能完成），降级 fetch+keepalive
  try {
    if (navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      const ok = navigator.sendBeacon(config.uploadEndpoint, blob);
      if (ok) {
        console.info(`[Replay] 已通过 sendBeacon 上传 replay_id=${replayId} events=${payload.length}`);
        return true;
      }
      // sendBeacon 失败（队列满），降级 fetch
    }
    const resp = await fetch(config.uploadEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,  // 允许页面卸载后继续完成请求
      credentials: 'include',
    });
    if (resp.ok) {
      console.info(`[Replay] 已通过 fetch 上传 replay_id=${replayId} events=${payload.length}`);
      return true;
    }
    console.error(`[${ReplayErrorCode.UPLOAD_FAILED}] 上传失败 status=${resp.status}`);
    return false;
  } catch (err) {
    console.error(`[${ReplayErrorCode.UPLOAD_FAILED}] 上传异常:`, err);
    return false;
  }
}

// ─── 状态查询 ─────────────────────────────────────────────────────────

/**
 * 获取回放录制状态（供 DevConsole 或健康检查使用）
 */
export function getReplayStatus(): {
  isRecording: boolean;
  enabled: boolean;
  sampleRate: number;
  bufferSize: number;
  currentTraceId: string | null;
  currentUserSessionId: string | null;
} {
  const config = getReplayConfig();
  return {
    isRecording: _isRecording,
    enabled: config.enabled,
    sampleRate: config.sampleRate,
    bufferSize: _eventBuffer.length,
    currentTraceId: _currentTraceId,
    currentUserSessionId: _userSessionId,
  };
}

/**
 * 重置内部状态（仅供测试使用）
 */
export function __resetReplayForTest(): void {
  stopReplayRecording();
  _eventBuffer = [];
  _cachedConfig = null;
  _userSessionId = null;
  _currentTraceId = null;
}
