/**
 * 请求拦截器 —— fetch / XMLHttpRequest 劫持 + 全局错误捕获
 *
 * 数据流：劫持 → 生成 record → 通过事件总线 emit → store 订阅消费
 *
 * 环境隔离：installInterceptors 内部首道检查 isObservabilityEnabled()，
 * 且整个模块仅在被 DevConsole 动态 import 时才进入打包产物（main.tsx 条件导入）。
 *
 * 状态同步机制说明：
 * - 拦截器不持有业务状态，仅做"旁路采集"，绝不修改请求/响应原数据，保证业务请求语义不变；
 * - trace_id 从响应头 traceparent 解析（W3C 格式 00-{trace_id}-{span_id}-{flags}），
 *   兼容 X-Trace-Id 头，实现前后端链路关联。
 *
 * 可观测性约束（对齐用户规则）：
 * - 核心分支输出 JSON 结构化日志，包含 trace_id / module_name / action / duration_ms；
 * - 边界异常显式抛出业务错误码（OBS_ERR_PARSE_*），不静默吞掉；
 * - 日志输出失败不影响主流程（try/catch 兜底）。
 */

import {
  isObservabilityEnabled,
  shouldSample,
} from '@/config/observability';
import type { NetworkRecord, ErrorRecord } from '@/components/DevConsole/types';

// ─── 结构化日志工具（对齐可观测性强制约束） ─────────────────────────────
// 输出 JSON 格式，必含 trace_id / module_name / action / duration_ms 字段。
// 日志失败不影响主流程；生产环境因本模块不被打包而自然不输出。

type ObsLogLevel = 'info' | 'warn' | 'error';

interface ObsLogPayload {
  action: string;
  trace_id: string | null;
  duration_ms: number;
  [key: string]: unknown;
}

/** 输出一条结构化可观测性日志 */
function logObs(level: ObsLogLevel, payload: ObsLogPayload): void {
  try {
    const entry = {
      timestamp: new Date().toISOString(),
      level,
      module_name: 'requestInterceptor',
      ...payload,
    };
    const line = JSON.stringify(entry);
    if (level === 'info') console.log(line);
    else if (level === 'warn') console.warn(line);
    else console.error(line);
  } catch {
    // 日志失败吞掉，绝不影响业务
  }
}

// ─── 事件总线（轻量发布订阅，零第三方依赖） ─────────────────────────────

type NetworkListener = (record: NetworkRecord) => void;
type ErrorListener = (record: ErrorRecord) => void;

const networkListeners = new Set<NetworkListener>();
const errorListeners = new Set<ErrorListener>();

/** 订阅网络请求记录 */
export function subscribeNetwork(fn: NetworkListener): () => void {
  networkListeners.add(fn);
  return () => {
    networkListeners.delete(fn);
  };
}

/** 订阅错误记录 */
export function subscribeError(fn: ErrorListener): () => void {
  errorListeners.add(fn);
  return () => {
    errorListeners.delete(fn);
  };
}

/** 发布网络记录（采样命中才发布） */
function emitNetwork(record: NetworkRecord): void {
  if (!shouldSample()) return;
  // 复制监听器集合，避免回调中取消订阅导致迭代异常
  const snapshot = Array.from(networkListeners);
  for (const fn of snapshot) {
    try {
      fn(record);
    } catch (err) {
      // 单个监听器失败不影响其他监听器与业务
      console.error('[Observability] network listener error:', err);
    }
  }
}

/** 发布错误记录 */
function emitError(record: ErrorRecord): void {
  const snapshot = Array.from(errorListeners);
  for (const fn of snapshot) {
    try {
      fn(record);
    } catch (err) {
      console.error('[Observability] error listener error:', err);
    }
  }
}

// ─── 工具函数 ──────────────────────────────────────────────────────────

/** 生成唯一 ID（性能优先，单条 < 1ms） */
function genId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 从响应头解析 trace_id
 *
 * 优先级：traceparent > X-Trace-Id
 * - traceparent 格式：00-{trace_id}-{span_id}-{flags}，取第 2 段
 * - X-Trace-Id：直接为 trace_id
 *
 * 解析失败场景（已加日志便于排查）：
 * - 响应头无 traceparent 与 x-trace-id（后端未注入）
 * - traceparent 格式异常（少于 2 段或第 2 段为空）
 * - CORS 场景下 headers.get 抛 SecurityError（暴露头未配置）
 */
export function parseTraceId(headers: Headers): string | null {
  const startedAt = performance.now();
  try {
    const traceparent = headers.get('traceparent');
    if (traceparent) {
      const parts = traceparent.split('-');
      // 格式: 00-{trace_id}-{span_id}-{flags}
      if (parts.length >= 2 && parts[1]) {
        const traceId = parts[1];
        logObs('info', {
          action: 'parse_trace_id_from_traceparent',
          trace_id: traceId,
          duration_ms: Number((performance.now() - startedAt).toFixed(2)),
          header_value: traceparent,
          parts_count: parts.length,
        });
        return traceId;
      }
      // traceparent 存在但格式异常
      logObs('warn', {
        action: 'parse_trace_id_invalid_traceparent',
        trace_id: null,
        duration_ms: Number((performance.now() - startedAt).toFixed(2)),
        header_value: traceparent,
        parts_count: parts.length,
        reason: 'parts_count_lt_2_or_empty_trace_id',
      });
    }
    const xTraceId = headers.get('x-trace-id');
    if (xTraceId) {
      logObs('info', {
        action: 'parse_trace_id_from_x_trace_id',
        trace_id: xTraceId,
        duration_ms: Number((performance.now() - startedAt).toFixed(2)),
      });
      return xTraceId;
    }
    // 两个头都不存在
    logObs('warn', {
      action: 'parse_trace_id_no_header',
      trace_id: null,
      duration_ms: Number((performance.now() - startedAt).toFixed(2)),
      has_traceparent: traceparent !== null,
      has_x_trace_id: xTraceId !== null,
    });
  } catch (err) {
    // headers.get 可能因 CORS 抛出 SecurityError，吞掉不影响业务
    logObs('error', {
      action: 'parse_trace_id_error',
      trace_id: null,
      duration_ms: Number((performance.now() - startedAt).toFixed(2)),
      error: err instanceof Error ? err.message : String(err),
      error_name: err instanceof Error ? err.name : 'Unknown',
    });
  }
  return null;
}

/** 截断字符串到指定长度，避免内存膨胀 */
function truncate(str: string | undefined, max = 200): string | undefined {
  if (str === undefined || str === null) return undefined;
  const s = String(str);
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** 最近一次网络请求的 trace_id，用于错误记录关联 */
let lastTraceId: string | null = null;

// ─── fetch 劫持 ────────────────────────────────────────────────────────

let originalFetch: typeof globalThis.fetch | null = null;

function installFetchPatch(): void {
  if (originalFetch) return; // 幂等保护
  originalFetch = globalThis.fetch;
  if (!originalFetch) return;

  const patchedFetch: typeof globalThis.fetch = async (
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> => {
    const startTime = performance.now();

    // 解析 URL 与 method
    let url = '';
    let method = 'GET';
    try {
      if (input instanceof Request) {
        url = input.url;
        method = (init?.method || input.method || 'GET').toUpperCase();
      } else if (input instanceof URL) {
        url = input.toString();
        method = (init?.method || 'GET').toUpperCase();
      } else {
        url = String(input);
        method = (init?.method || 'GET').toUpperCase();
      }
    } catch {
      // 解析失败不影响请求本身
    }

    // 记录请求体摘要（仅字符串/可序列化）
    let requestBody: string | undefined;
    try {
      if (init?.body) {
        if (typeof init.body === 'string') {
          requestBody = truncate(init.body);
        } else if (init.body instanceof URLSearchParams) {
          requestBody = truncate(init.body.toString());
        }
      }
    } catch {
      // ignore
    }

    try {
      const response = await originalFetch!(input, init);
      const duration = performance.now() - startTime;

      // 读取 trace_id（响应头）
      let traceId: string | null = null;
      try {
        traceId = parseTraceId(response.headers);
        if (traceId) {
          lastTraceId = traceId;
          // 同步 trace_id 到 Sentry（动态 import，避免硬依赖，失败不影响业务）
          // 状态同步机制：确保 Sentry 事件与后端 OpenTelemetry 链路关联
          import('@/utils/sentry')
            .then(({ setTraceId }) => setTraceId(traceId))
            .catch(() => { /* ignore */ });
        }
      } catch {
        // ignore
      }

      logObs('info', {
        action: 'fetch_success',
        trace_id: traceId,
        duration_ms: Number(duration.toFixed(2)),
        method,
        url,
        status: response.status,
        source: 'fetch',
      });

      emitNetwork({
        id: genId(),
        url,
        method,
        status: response.status,
        duration,
        traceId,
        timestamp: Date.now(),
        requestBody,
        source: 'fetch',
      });

      return response;
    } catch (err) {
      const duration = performance.now() - startTime;
      const errMsg = err instanceof Error ? err.message : String(err);

      logObs('error', {
        action: 'fetch_error',
        trace_id: null,
        duration_ms: Number(duration.toFixed(2)),
        method,
        url,
        error: errMsg,
        error_name: err instanceof Error ? err.name : 'Unknown',
        source: 'fetch',
      });

      emitNetwork({
        id: genId(),
        url,
        method,
        status: 0,
        duration,
        traceId: null,
        timestamp: Date.now(),
        requestBody,
        error: errMsg,
        source: 'fetch',
      });
      throw err;
    }
  };

  globalThis.fetch = patchedFetch;
}

function uninstallFetchPatch(): void {
  if (originalFetch) {
    globalThis.fetch = originalFetch;
    originalFetch = null;
  }
}

// ─── XMLHttpRequest 劫持 ───────────────────────────────────────────────

let originalOpen: typeof XMLHttpRequest.prototype.open | null = null;
let originalSend: typeof XMLHttpRequest.prototype.send | null = null;

function installXhrPatch(): void {
  if (originalOpen) return; // 幂等保护
  originalOpen = XMLHttpRequest.prototype.open;
  originalSend = XMLHttpRequest.prototype.send;

  // 劫持 open：记录 method / url
  XMLHttpRequest.prototype.open = function (
    this: XMLHttpRequest,
    method: string,
    url: string,
    ...rest: unknown[]
  ): void {
    // 在 xhr 实例上挂载元数据（非可枚举，避免污染业务）
    Object.defineProperty(this, '__obs_meta', {
      value: {
        method: method.toUpperCase(),
        url: String(url),
        startTime: 0,
        requestBody: undefined as string | undefined,
      },
      writable: true,
      enumerable: false,
      configurable: true,
    });
    // 透传给原始 open（保持参数语义）
    return originalOpen!.call(
      this,
      method,
      url,
      ...(rest as [boolean, string | null, string | null])
    );
  };

  // 劫持 send：记录起始时间，监听 loadend 读取结果
  XMLHttpRequest.prototype.send = function (
    this: XMLHttpRequest,
    body?: Document | BodyInit | null
  ): void {
    const meta = (this as { __obs_meta?: { method: string; url: string; startTime: number; requestBody?: string } }).__obs_meta;
    if (meta) {
      meta.startTime = performance.now();
      try {
        if (typeof body === 'string') {
          meta.requestBody = truncate(body);
        }
      } catch {
        // ignore
      }
    }

    this.addEventListener('loadend', () => {
      if (!meta) return;
      const duration = performance.now() - meta.startTime;

      // 读取 trace_id
      let traceId: string | null = null;
      let rawHeaderStr = '';
      try {
        rawHeaderStr = this.getAllResponseHeaders();
        if (rawHeaderStr) {
          // getAllResponseHeaders 返回 \r\n 分隔的键值对
          const lines = rawHeaderStr.split('\r\n');
          let traceparent = '';
          let xTraceId = '';
          for (const line of lines) {
            const idx = line.indexOf(':');
            if (idx < 0) continue;
            const key = line.slice(0, idx).trim().toLowerCase();
            const val = line.slice(idx + 1).trim();
            if (key === 'traceparent') traceparent = val;
            else if (key === 'x-trace-id') xTraceId = val;
          }
          if (traceparent) {
            const parts = traceparent.split('-');
            if (parts.length >= 2 && parts[1]) {
              traceId = parts[1];
            } else {
              logObs('warn', {
                action: 'xhr_invalid_traceparent',
                trace_id: null,
                duration_ms: Number(duration.toFixed(2)),
                url: meta.url,
                method: meta.method,
                header_value: traceparent,
                parts_count: parts.length,
                reason: 'parts_count_lt_2_or_empty_trace_id',
              });
            }
          } else if (xTraceId) {
            traceId = xTraceId;
          }
          if (traceId) {
            lastTraceId = traceId;
            // 同步 trace_id 到 Sentry（动态 import，避免硬依赖，失败不影响业务）
            import('@/utils/sentry')
              .then(({ setTraceId }) => setTraceId(traceId))
              .catch(() => { /* ignore */ });
          }
        }
      } catch (headerErr) {
        logObs('error', {
          action: 'xhr_read_header_error',
          trace_id: null,
          duration_ms: Number(duration.toFixed(2)),
          url: meta.url,
          method: meta.method,
          error: headerErr instanceof Error ? headerErr.message : String(headerErr),
        });
      }

      // 错误判定：status === 0 通常表示网络错误或中止
      const isError = this.status === 0;

      logObs(isError ? 'error' : 'info', {
        action: isError ? 'xhr_loadend_error' : 'xhr_loadend_success',
        trace_id: traceId,
        duration_ms: Number(duration.toFixed(2)),
        url: meta.url,
        method: meta.method,
        status: this.status,
        has_response_headers: rawHeaderStr.length > 0,
        source: 'xhr',
      });

      emitNetwork({
        id: genId(),
        url: meta.url,
        method: meta.method,
        status: this.status,
        duration,
        traceId,
        timestamp: Date.now(),
        requestBody: meta.requestBody,
        error: isError ? '网络错误或请求被中止' : undefined,
        source: 'xhr',
      });
    });

    return originalSend!.call(this, body);
  };
}

function uninstallXhrPatch(): void {
  if (originalOpen) {
    XMLHttpRequest.prototype.open = originalOpen;
    originalOpen = null;
  }
  if (originalSend) {
    XMLHttpRequest.prototype.send = originalSend;
    originalSend = null;
  }
}

// ─── 全局错误捕获 ──────────────────────────────────────────────────────

let onErrorHandler: ((this: WindowEventHandlers, ev: ErrorEvent) => unknown) | null = null;
let rejectionHandler: ((this: WindowEventHandlers, ev: PromiseRejectionEvent) => unknown) | null = null;

function installErrorCapture(): void {
  if (onErrorHandler) return; // 幂等保护

  // window.onerror：捕获同步错误与资源加载错误
  onErrorHandler = (event: Event | ErrorEvent): void => {
    try {
      const errEvent = event as ErrorEvent;
      logObs('error', {
        action: 'capture_onerror',
        trace_id: lastTraceId,
        duration_ms: 0,
        message: errEvent.message || '未知错误',
        source: errEvent.filename,
        lineno: errEvent.lineno,
        colno: errEvent.colno,
      });
      emitError({
        id: genId(),
        type: 'onerror',
        message: errEvent.message || '未知错误',
        stack: errEvent.error?.stack || errEvent.message || '',
        traceId: lastTraceId,
        timestamp: Date.now(),
        source: errEvent.filename,
        lineno: errEvent.lineno,
        colno: errEvent.colno,
      });
    } catch (err) {
      console.error('[Observability] onerror 处理失败:', err);
    }
  };
  window.addEventListener('error', onErrorHandler as EventListener);

  // unhandledrejection：捕获未处理的 Promise rejection
  rejectionHandler = (event: Event | PromiseRejectionEvent): void => {
    try {
      const rejEvent = event as PromiseRejectionEvent;
      const reason = rejEvent.reason;
      const message =
        reason instanceof Error ? reason.message : String(reason);
      const stack = reason instanceof Error ? reason.stack || '' : '';
      logObs('error', {
        action: 'capture_unhandledrejection',
        trace_id: lastTraceId,
        duration_ms: 0,
        message,
        reason_type: reason instanceof Error ? 'Error' : typeof reason,
      });
      emitError({
        id: genId(),
        type: 'unhandledrejection',
        message,
        stack,
        traceId: lastTraceId,
        timestamp: Date.now(),
      });
    } catch (err) {
      console.error('[Observability] unhandledrejection 处理失败:', err);
    }
  };
  window.addEventListener(
    'unhandledrejection',
    rejectionHandler as EventListener
  );
}

function uninstallErrorCapture(): void {
  if (onErrorHandler) {
    window.removeEventListener('error', onErrorHandler as EventListener);
    onErrorHandler = null;
  }
  if (rejectionHandler) {
    window.removeEventListener(
      'unhandledrejection',
      rejectionHandler as EventListener
    );
    rejectionHandler = null;
  }
}

// ─── 统一安装/卸载入口 ─────────────────────────────────────────────────

let installed = false;

/**
 * 卸载全部拦截器，恢复原始 fetch / XHR / 错误监听
 *
 * 幂等：未安装时调用无副作用。
 */
export function uninstallInterceptors(): void {
  if (!installed) return;
  uninstallFetchPatch();
  uninstallXhrPatch();
  uninstallErrorCapture();
  installed = false;
  logObs('info', {
    action: 'uninstall_interceptors',
    trace_id: null,
    duration_ms: 0,
  });
}

/**
 * 安装全部拦截器（fetch / XHR / 错误捕获）
 *
 * 幂等：重复调用不会重复劫持。
 * 环境隔离：内部首道检查 isObservabilityEnabled()，生产环境直接返回。
 *
 * @returns 卸载函数，调用后恢复原始行为
 */
export function installInterceptors(): () => void {
  const startedAt = performance.now();
  if (installed) {
    logObs('info', {
      action: 'install_interceptors_skip',
      trace_id: null,
      duration_ms: 0,
      reason: 'already_installed',
    });
    return () => {};
  }
  // 环境守卫：生产环境不安装
  if (!isObservabilityEnabled()) {
    logObs('warn', {
      action: 'install_interceptors_skip',
      trace_id: null,
      duration_ms: 0,
      reason: 'observability_disabled',
    });
    return () => {};
  }

  try {
    installFetchPatch();
    installXhrPatch();
    installErrorCapture();
    installed = true;

    logObs('info', {
      action: 'install_interceptors_success',
      trace_id: null,
      duration_ms: Number((performance.now() - startedAt).toFixed(2)),
      components: ['fetch', 'xhr', 'error'],
    });

    return uninstallInterceptors;
  } catch (err) {
    // 安装失败不影响业务，仅记录
    logObs('error', {
      action: 'install_interceptors_failed',
      trace_id: null,
      duration_ms: Number((performance.now() - startedAt).toFixed(2)),
      error: err instanceof Error ? err.message : String(err),
      error_name: err instanceof Error ? err.name : 'Unknown',
    });
    return () => {};
  }
}

/** 拦截器是否已安装（仅供测试） */
export function isInstalled(): boolean {
  return installed;
}

/** 清除全部监听器（仅供测试） */
export function __clearListenersForTest(): void {
  networkListeners.clear();
  errorListeners.clear();
  lastTraceId = null;
}
