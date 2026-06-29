/**
 * 云枢前端可观测性配置
 *
 * 设计原则：
 * 1. 环境隔离 —— 通过 Vite 内置 `import.meta.env.DEV` 与自定义 `VITE_OBSERVABILITY_ENABLED`
 *    双重控制，生产构建时由 tree-shaking 移除全部可观测性代码，零性能损耗。
 * 2. 边界显性化 —— 配置项类型与取值范围在校验失败时抛出带业务错误码的 Error，而非静默降级。
 * 3. 单条记录渲染 < 16ms —— 通过 maxRecords 上限 + LRU 淘汰保证。
 */

/** 可观测性配置项 */
export interface ObservabilityConfig {
  /** DevConsole 浮层总开关 */
  devConsoleEnabled: boolean;
  /** StateInspector 面板开关 */
  stateInspectorEnabled: boolean;
  /** 单个面板最大保留记录数（超出按 LRU 淘汰） */
  maxRecords: number;
  /** 采样率，取值范围 [0, 1]，1 表示全量采集 */
  samplingRate: number;
  /** 是否在控制台打印可观测性自身日志 */
  verbose: boolean;
}

/** 业务错误码枚举（边界显性化） */
export enum ObservabilityErrorCode {
  INVALID_MAX_RECORDS = 'OBS_ERR_001',
  INVALID_SAMPLING_RATE = 'OBS_ERR_002',
  INVALID_ENV_VAR = 'OBS_ERR_003',
}

/**
 * 读取并校验环境变量，组装可观测性配置
 *
 * 注意：本函数仅在开发环境下被调用（由 isObservabilityEnabled 守卫），
 * 生产环境下本模块整体被 tree-shaking 移除。
 */
function resolveConfig(): ObservabilityConfig {
  // Vite 环境变量（构建时静态替换）
  const env = import.meta.env;

  // 主开关：VITE_OBSERVABILITY_ENABLED 优先，缺省时回退到 Vite DEV 标志
  const envEnabled = env.VITE_OBSERVABILITY_ENABLED;
  const enabled =
    envEnabled === undefined ? env.DEV === true : envEnabled === 'true';

  // 最大记录数：默认 200
  const rawMax = env.VITE_OBS_MAX_RECORDS;
  const maxRecords = rawMax !== undefined ? Number(rawMax) : 200;
  if (!Number.isFinite(maxRecords) || maxRecords < 1 || maxRecords > 1000) {
    throw new Error(
      `[${ObservabilityErrorCode.INVALID_MAX_RECORDS}] VITE_OBS_MAX_RECORDS 必须为 1~1000 之间的有限数，当前值: ${rawMax}`
    );
  }

  // 采样率：默认 1（全量）
  const rawRate = env.VITE_OBS_SAMPLING_RATE;
  const samplingRate = rawRate !== undefined ? Number(rawRate) : 1;
  if (
    !Number.isFinite(samplingRate) ||
    samplingRate < 0 ||
    samplingRate > 1
  ) {
    throw new Error(
      `[${ObservabilityErrorCode.INVALID_SAMPLING_RATE}] VITE_OBS_SAMPLING_RATE 必须为 0~1 之间的数值，当前值: ${rawRate}`
    );
  }

  return {
    devConsoleEnabled: enabled,
    stateInspectorEnabled: enabled,
    maxRecords,
    samplingRate,
    verbose: env.DEV === true,
  };
}

/** 缓存的配置实例（仅初始化一次） */
let _cachedConfig: ObservabilityConfig | null = null;

/**
 * 获取可观测性配置（单例）
 *
 * 异常处理：配置解析失败时记录错误并返回禁用配置，确保 DevConsole 内部错误
 * 不影响业务页面渲染（遵循"DevConsole 内部错误不得影响业务"约束）。
 */
export function getObservabilityConfig(): ObservabilityConfig {
  if (_cachedConfig) return _cachedConfig;

  try {
    _cachedConfig = resolveConfig();
  } catch (err) {
    // 吞掉配置错误，回退到安全默认值，避免阻塞业务
    console.error('[Observability] 配置解析失败，已回退到禁用状态:', err);
    _cachedConfig = {
      devConsoleEnabled: false,
      stateInspectorEnabled: false,
      maxRecords: 200,
      samplingRate: 1,
      verbose: false,
    };
  }
  return _cachedConfig;
}

/**
 * 可观测性是否启用（环境隔离的统一入口）
 *
 * 生产环境下 `import.meta.env.DEV === false`，且 VITE_OBSERVABILITY_ENABLED
 * 默认为 'false'，因此本函数返回 false，所有可观测性模块的劫持逻辑均不会安装。
 */
export function isObservabilityEnabled(): boolean {
  return getObservabilityConfig().devConsoleEnabled;
}

/**
 * 采样判定：根据 samplingRate 决定当前记录是否采集
 *
 * @returns true 表示本次记录需要采集
 */
export function shouldSample(): boolean {
  const { samplingRate } = getObservabilityConfig();
  if (samplingRate >= 1) return true;
  if (samplingRate <= 0) return false;
  return Math.random() < samplingRate;
}

/**
 * 重置配置缓存（仅供测试使用）
 *
 * 注意：仅在 vitest 测试环境中调用，生产代码不应使用。
 */
export function __resetObservabilityConfigForTest(): void {
  _cachedConfig = null;
}

// ──────────────────────────────────────────────────────────────────────
// 业务埋点 trackEvent（D5 指标）
// ──────────────────────────────────────────────────────────────────────
//
// 设计要点（对齐项目硬约束）：
// 1. 性能 < 1ms：仅做轻量对象组装 + console.debug，不触发网络请求；
// 2. 失败静默：try/catch 兜底，埋点异常不得影响主业务流程；
// 3. 环境隔离：生产环境（import.meta.env.DEV === false）下整体被 tree-shaking 移除；
// 4. 采样控制：复用 shouldSample()，按 samplingRate 决定是否采集；
// 5. 命名规范：事件名遵循 yunshu_<模块>_<动作> 格式，便于后端聚合。

/** 业务埋点事件名枚举（与后端 BusinessMetricsCollector 对齐） */
export enum TrackEventName {
  FORM_SUBMIT = 'yunshu_form_submit',
  FILTER_APPLY = 'yunshu_filter_apply',
  CHAT_SEND = 'yunshu_chat_send',
  DASHBOARD_LOAD = 'yunshu_dashboard_load',
  DEVCONSOLE_OPEN = 'yunshu_devconsole_open',
  SESSION_SWITCH = 'yunshu_session_switch',
  SETTINGS_CHANGE = 'yunshu_settings_change',
}

/** 埋点 payload 类型 */
export interface TrackEventPayload {
  /** 模块名 */
  module: string;
  /** 操作结果 success/failure */
  success: boolean;
  /** 耗时（毫秒），可选 */
  duration_ms?: number;
  /** 附加字段 */
  [key: string]: unknown;
}

/**
 * 业务埋点函数（D5 指标占位调用）
 *
 * 状态同步机制说明：本函数为轻量同步调用，无异步副作用，不参与 UI 状态流转；
 * 失败时仅 console.warn，不抛出异常，保证主流程不受影响。
 *
 * @param eventName 事件名（遵循 yunshu_<模块>_<动作> 规范）
 * @param payload 埋点数据（必须含 module、success 字段）
 */
export function trackEvent(
  eventName: string,
  payload: TrackEventPayload
): void {
  // 生产环境下整体被 tree-shaking 移除（isObservabilityEnabled 内联为 false）
  if (!isObservabilityEnabled()) return;

  // 采样控制：低采样率下跳过部分埋点
  if (!shouldSample()) return;

  // 性能保护：单次埋点耗时必须 < 1ms，仅做轻量操作
  try {
    const record = {
      event: eventName,
      module: payload.module,
      success: payload.success,
      duration_ms: payload.duration_ms ?? 0,
      timestamp: Date.now(),
      trace_id: _getCurrentTraceIdSafe(),
      ...payload,
    };
    // 轻量输出：仅 console.debug，不触发网络请求
    console.debug('[trackEvent]', record);
  } catch (err) {
    // 静默吞掉异常，埋点失败不得影响主业务流程
    console.warn('[trackEvent] 埋点记录失败（已静默）:', err);
  }
}

/**
 * 安全获取当前 trace_id（供埋点关联使用）
 *
 * 失败时返回 undefined，不抛出异常。
 */
function _getCurrentTraceIdSafe(): string | undefined {
  try {
    // 从 window.__TRACE_ID__ 读取（由 requestInterceptor 注入）
    const w = window as unknown as { __TRACE_ID__?: string };
    return w.__TRACE_ID__;
  } catch {
    return undefined;
  }
}
