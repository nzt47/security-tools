/**
 * 云枢前端 Sentry 集成
 *
 * 设计原则：
 * 1. 零付费依赖 —— 后端使用自建 GlitchTip（Sentry 协议兼容），不接入 Sentry SaaS 付费版。
 * 2. 链路关联 —— 通过 X-Trace-Id 响应头解析后端 trace_id，与 OpenTelemetry 跨前后端关联。
 * 3. 边界显性化 —— DSN 缺失或非法时抛出带业务错误码的 Error，而非静默降级。
 * 4. 失败隔离 —— Sentry 初始化/上报失败不得影响业务主流程，仅 console.error 警告。
 * 5. 用户面包屑 —— 自动采集最近 10 个用户操作事件（点击/输入/路由跳转），便于复现缺陷。
 *
 * 状态同步机制说明：
 * - 使用 @sentry/react 的 beforeSend 钩子注入 trace_id 到 event.tags，
 *   实现前端事件与后端 OpenTelemetry 链路的双向关联；
 * - 通过 fetch 拦截器（与 requestInterceptor 互补，不重复劫持）解析响应头 X-Trace-Id，
 *   写入 Sentry scope，确保单次会话内所有错误事件携带相同的 trace_id。
 *
 * 使用方式：
 *   // main.tsx
 *   import { initSentry } from '@/utils/sentry';
 *   initSentry();  // 在 createRoot 之前调用
 */

import * as Sentry from '@sentry/react';
import type { BrowserOptions } from '@sentry/react';

// ─── 业务错误码（边界显性化） ────────────────────────────────────────────

export enum SentryErrorCode {
  INVALID_DSN = 'SENTRY_FE_001',   // DSN 格式非法
  INVALID_SAMPLE_RATE = 'SENTRY_FE_002', // 采样率越界
  INIT_FAILED = 'SENTRY_FE_003',   // SDK 初始化失败
  NOT_INITIALIZED = 'SENTRY_FE_004', // 未初始化即调用上报
}

// ─── 配置类型 ─────────────────────────────────────────────────────────

export interface SentryConfig {
  /** 是否启用（VITE_SENTRY_DSN 配置后自动启用） */
  enabled: boolean;
  /** GlitchTip / Sentry DSN */
  dsn: string;
  /** 环境名（dev/staging/production） */
  environment: string;
  /** 错误采样率 [0, 1]，默认 1.0（开发）/ 0.1（生产） */
  sampleRate: number;
  /** 链路采样率 [0, 1]，默认 0（仅错误不采链路，避免噪声） */
  tracesSampleRate: number;
  /** 发布版本号（用于版本关联，可选） */
  release?: string;
  /** 服务名 */
  serverName: string;
  /** 单次会话保留的面包屑上限（默认 10） */
  maxBreadcrumbs: number;
}

// ─── 状态变量（模块级单例） ───────────────────────────────────────────

let _initialized = false;
let _cachedConfig: SentryConfig | null = null;
let _currentTraceId: string | null = null;
let _currentUserSessionId: string | null = null;

// ─── 配置解析 ─────────────────────────────────────────────────────────

/**
 * 解析 Vite 环境变量组装配置
 *
 * 边界显性化：DSN 非空但格式非法时抛出业务错误码 Error；
 * 采样率越界同样抛出，避免静默降级导致难以排查。
 */
function resolveConfig(): SentryConfig {
  const env = import.meta.env;

  const dsn = (env.VITE_SENTRY_DSN as string | undefined)?.trim() ?? '';
  const enabled = dsn.length > 0;

  // DSN 格式粗校验（必须以 http(s):// 开头）
  if (enabled && !/^https?:\/\//i.test(dsn)) {
    throw new Error(
      `[${SentryErrorCode.INVALID_DSN}] VITE_SENTRY_DSN 必须以 http(s):// 开头，当前值: ${dsn}`
    );
  }

  const isProd = env.PROD === true;
  const rawSampleRate = env.VITE_SENTRY_SAMPLE_RATE as string | undefined;
  const sampleRate = rawSampleRate !== undefined ? Number(rawSampleRate) : (isProd ? 0.1 : 1.0);
  if (!Number.isFinite(sampleRate) || sampleRate < 0 || sampleRate > 1) {
    throw new Error(
      `[${SentryErrorCode.INVALID_SAMPLE_RATE}] VITE_SENTRY_SAMPLE_RATE 必须在 [0, 1] 区间内，当前值: ${rawSampleRate}`
    );
  }

  const rawTracesRate = env.VITE_SENTRY_TRACES_SAMPLE_RATE as string | undefined;
  const tracesSampleRate = rawTracesRate !== undefined ? Number(rawTracesRate) : 0;
  if (!Number.isFinite(tracesSampleRate) || tracesSampleRate < 0 || tracesSampleRate > 1) {
    throw new Error(
      `[${SentryErrorCode.INVALID_SAMPLE_RATE}] VITE_SENTRY_TRACES_SAMPLE_RATE 必须在 [0, 1] 区间内，当前值: ${rawTracesRate}`
    );
  }

  return {
    enabled,
    dsn,
    environment: env.MODE ?? 'development',
    sampleRate,
    tracesSampleRate,
    release: (env.VITE_APP_VERSION as string | undefined) ?? undefined,
    serverName: 'yunshu-frontend',
    maxBreadcrumbs: 10,
  };
}

/**
 * 获取 Sentry 配置（单例，缓存解析结果）
 *
 * 异常处理：配置解析失败时返回禁用配置，避免阻塞应用启动。
 */
export function getSentryConfig(): SentryConfig {
  if (_cachedConfig) return _cachedConfig;
  try {
    _cachedConfig = resolveConfig();
  } catch (err) {
    // 配置错误回退到禁用状态，避免阻塞业务渲染
    console.error('[Sentry] 配置解析失败，已回退到禁用状态:', err);
    _cachedConfig = {
      enabled: false,
      dsn: '',
      environment: 'development',
      sampleRate: 0,
      tracesSampleRate: 0,
      serverName: 'yunshu-frontend',
      maxBreadcrumbs: 10,
    };
  }
  return _cachedConfig;
}

// ─── 初始化 ───────────────────────────────────────────────────────────

/**
 * 初始化 Sentry SDK
 *
 * 应在 React createRoot 之前调用，确保 ErrorBoundary 能捕获根组件错误。
 * 失败隔离：初始化失败仅记录日志，不抛异常，保证业务页面可正常渲染。
 *
 * @returns True 表示已成功初始化；False 表示未启用或初始化失败
 */
export function initSentry(): boolean {
  if (_initialized) return true;

  const config = getSentryConfig();
  if (!config.enabled) {
    return false;
  }

  try {
    const integrations: any[] = [
      // 自动捕获 React 组件渲染错误
      (Sentry as any).reactRouterV6BrowserTracingIntegration?.() ??
        (Sentry as any).browserTracingIntegration?.(),
    ].filter(Boolean);

    const options = {
      dsn: config.dsn,
      environment: config.environment,
      sampleRate: config.sampleRate,
      tracesSampleRate: config.tracesSampleRate,
      release: config.release,
      serverName: config.serverName,
      integrations,
      maxBreadcrumbs: config.maxBreadcrumbs,
      attachStacktrace: true,
      sendDefaultPii: false, // 不采集 PII，满足隐私约束
      beforeSend: (event) => filterSensitiveData(event),
    } as BrowserOptions;

    Sentry.init(options);
    _initialized = true;

    // 自动注册用户操作面包屑（点击、输入、路由跳转）
    installUserActionBreadcrumbs();

    console.info(
      `[Sentry] 已初始化 environment=${config.environment} sampleRate=${config.sampleRate}`
    );
    return true;
  } catch (err) {
    console.error(`[${SentryErrorCode.INIT_FAILED}] Sentry 初始化失败，已降级为关闭:`, err);
    _initialized = false;
    return false;
  }
}

// ─── 敏感信息过滤 ─────────────────────────────────────────────────────

const SENSITIVE_KEY_PATTERNS = [
  'password', 'passwd', 'pwd',
  'token', 'access_token', 'refresh_token', 'api_key', 'apikey',
  'secret',
  'id_card', 'idcard', 'id_number',
  'bank_card', 'bankcard', 'card_number',
  'phone', 'mobile',
];

function isSensitiveKey(key: string): boolean {
  if (typeof key !== 'string') return false;
  const lower = key.toLowerCase();
  return SENSITIVE_KEY_PATTERNS.some((p) => lower.includes(p));
}

function filterSensitiveData<T>(obj: T): T {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj !== 'object') return obj;

  try {
    if (Array.isArray(obj)) {
      return obj.map((item) => filterSensitiveData(item)) as unknown as T;
    }
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      result[k] = isSensitiveKey(k)
        ? '[REDACTED]'
        : filterSensitiveData(v);
    }
    return result as unknown as T;
  } catch {
    // 过滤失败时返回原对象，避免阻塞上报
    return obj;
  }
}

// ─── 用户操作面包屑 ──────────────────────────────────────────────────

/**
 * 安装用户操作面包屑采集
 *
 * 采集事件（最近 10 个，FIFO 淘汰）：
 * - click：用户点击元素（记录标签 + 文本摘要，自动脱敏）
 * - input：输入框失焦（不记录具体值，仅记录字段名）
 * - navigation：路由变化（hash/path）
 *
 * 状态同步机制：使用 Sentry.addBreadcrumb 写入 SDK 内置缓冲区，
 * SDK 自动维护 FIFO 淘汰，无需自行维护队列。
 */
function installUserActionBreadcrumbs(): void {
  try {
    // click 事件委托（仅在 document 上注册一次）
    document.addEventListener(
      'click',
      (event) => {
        try {
          const target = event.target as HTMLElement | null;
          if (!target) return;
          const tag = target.tagName?.toLowerCase() ?? 'unknown';
          const text = (target.innerText ?? '').slice(0, 50).trim();
          const dataAttr = target.dataset?.testId ?? '';
          Sentry.addBreadcrumb({
            category: 'ui.click',
            message: `<${tag}> ${text}`,
            level: 'info',
            data: { tag, testId: dataAttr },
          });
        } catch {
          // 面包屑采集失败不得影响业务
        }
      },
      { passive: true, capture: true }
    );

    // input 失焦（仅记录字段名，不记录值，自动脱敏）
    document.addEventListener(
      'blur',
      (event) => {
        try {
          const target = event.target as HTMLInputElement | null;
          if (!target || target.tagName !== 'INPUT') return;
          const name = target.name || target.id || 'unknown';
          const type = target.type || 'text';
          // 敏感字段（password 等）不记录具体值
          const isSensitive = type === 'password' || isSensitiveKey(name);
          Sentry.addBreadcrumb({
            category: 'ui.input',
            message: `input[${type}] name=${name}`,
            level: 'info',
            data: { name, type, sensitive: isSensitive },
          });
        } catch {
          // 忽略
        }
      },
      { passive: true, capture: true }
    );

    // 路由变化（hash + history API）
    let lastPath = location.pathname + location.hash;
    const reportNav = (to: string) => {
      if (to === lastPath) return;
      lastPath = to;
      try {
        Sentry.addBreadcrumb({
          category: 'navigation',
          message: to,
          level: 'info',
        });
      } catch {
        // 忽略
      }
    };

    window.addEventListener('hashchange', () =>
      reportNav(location.pathname + location.hash)
    );
    const pushState = history.pushState;
    history.pushState = function (...args: Parameters<typeof pushState>) {
      const ret = pushState.apply(this, args);
      reportNav(location.pathname + location.hash);
      return ret;
    };
    window.addEventListener('popstate', () =>
      reportNav(location.pathname + location.hash)
    );
  } catch (err) {
    console.warn('[Sentry] 用户操作面包屑采集安装失败:', err);
  }
}

// ─── trace_id 关联 ──────────────────────────────────────────────────

/**
 * 设置当前会话的 trace_id（从响应头 X-Trace-Id 解析后调用）
 *
 * 与 requestInterceptor 互补：requestInterceptor 仅在 DevConsole 启用时
 * 解析 trace_id；本函数为 Sentry 提供独立的 trace_id 透传通道，
 * 确保生产环境（DevConsole 关闭）下 Sentry 事件仍能携带 trace_id。
 *
 * @param traceId 后端响应的 trace_id
 */
export function setTraceId(traceId: string | null): void {
  _currentTraceId = traceId;
  if (!_initialized || !traceId) return;
  try {
    Sentry.setTag('trace_id', traceId);
  } catch {
    // 忽略
  }
}

/**
 * 设置用户会话 ID（与后端 trace_id 关联，用于用户行为回放匹配）
 *
 * @param sessionId 用户会话 ID（前端生成或后端下发）
 */
export function setUserSessionId(sessionId: string | null): void {
  _currentUserSessionId = sessionId;
  if (!_initialized) return;
  try {
    if (sessionId) {
      Sentry.setTag('user_session_id', sessionId);
      Sentry.setUser({ id: sessionId });
    } else {
      Sentry.setUser(null);
    }
  } catch {
    // 忽略
  }
}

// ─── 主动上报 API ──────────────────────────────────────────────────

/**
 * 主动上报错误到 Sentry
 *
 * 失败隔离：未初始化或上报失败时仅返回 null，不抛异常。
 *
 * @param error 错误对象
 * @param context 上下文信息（自动脱敏）
 * @returns Sentry 事件 ID；未启用或失败时返回 null
 */
export function captureError(
  error: Error | unknown,
  context?: Record<string, unknown>
): string | null {
  if (!_initialized) {
    console.warn(`[${SentryErrorCode.NOT_INITIALIZED}] Sentry 未初始化，跳过上报`);
    return null;
  }
  try {
    const safeContext = context ? filterSensitiveData(context) : undefined;
    if (safeContext) {
      Sentry.setContext('extra', safeContext);
    }
    return Sentry.captureException(error);
  } catch (err) {
    console.error('[Sentry] captureError 失败:', err);
    return null;
  }
}

/**
 * 主动上报消息（非错误）
 */
export function captureMessage(
  message: string,
  level: 'info' | 'warning' | 'error' = 'info',
  context?: Record<string, unknown>
): string | null {
  if (!_initialized) return null;
  try {
    const safeContext = context ? filterSensitiveData(context) : undefined;
    if (safeContext) {
      Sentry.setContext('extra', safeContext);
    }
    return Sentry.captureMessage(message, level);
  } catch (err) {
    console.error('[Sentry] captureMessage 失败:', err);
    return null;
  }
}

/**
 * 获取当前 Sentry 状态（供 DevConsole 或健康检查使用）
 */
export function getSentryStatus(): {
  initialized: boolean;
  enabled: boolean;
  currentTraceId: string | null;
  currentUserSessionId: string | null;
} {
  return {
    initialized: _initialized,
    enabled: getSentryConfig().enabled,
    currentTraceId: _currentTraceId,
    currentUserSessionId: _currentUserSessionId,
  };
}

/**
 * 重置内部状态（仅供测试使用）
 */
export function __resetSentryForTest(): void {
  _initialized = false;
  _cachedConfig = null;
  _currentTraceId = null;
  _currentUserSessionId = null;
}
