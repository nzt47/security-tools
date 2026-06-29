/**
 * DevConsole 共享类型定义
 *
 * 这些类型同时被 requestInterceptor（数据生产者）与 store（数据消费者）引用，
 * 保证两端数据结构一致。
 */

/** 网络请求记录 */
export interface NetworkRecord {
  /** 唯一记录 ID */
  id: string;
  /** 请求 URL */
  url: string;
  /** HTTP 方法（大写） */
  method: string;
  /** HTTP 状态码，0 表示未收到响应（如网络错误/CORS） */
  status: number;
  /** 请求耗时（毫秒） */
  duration: number;
  /** 后端链路 trace_id（从 traceparent / X-Trace-Id 响应头解析） */
  traceId: string | null;
  /** 记录生成时间戳 */
  timestamp: number;
  /** 请求体摘要（仅记录字符串前 200 字符，避免内存膨胀） */
  requestBody?: string;
  /** 响应体摘要（仅记录字符串前 200 字符） */
  responseBody?: string;
  /** 错误信息（请求失败时填充） */
  error?: string;
  /** 请求来源标记（fetch / xhr） */
  source: 'fetch' | 'xhr';
}

/** 错误记录 */
export interface ErrorRecord {
  /** 唯一记录 ID */
  id: string;
  /** 错误类型（onerror / unhandledrejection） */
  type: 'onerror' | 'unhandledrejection';
  /** 错误消息 */
  message: string;
  /** 错误堆栈 */
  stack: string;
  /** 关联 trace_id（若可从最近一次请求推断） */
  traceId: string | null;
  /** 记录生成时间戳 */
  timestamp: number;
  /** 错误来源文件 */
  source?: string;
  /** 行号 */
  lineno?: number;
  /** 列号 */
  colno?: number;
}

/** 性能记录 */
export interface PerfRecord {
  /** 唯一记录 ID */
  id: string;
  /** 指标名称 */
  name: string;
  /** 耗时（毫秒） */
  duration: number;
  /** 记录生成时间戳 */
  timestamp: number;
  /** 附加详情 */
  detail?: Record<string, unknown>;
}

/** DevConsole 面板类型 */
export type DevConsoleTab = 'network' | 'error' | 'performance';

/** 状态快照（StateInspector 使用） */
export interface StateSnapshot {
  /** 状态键名 */
  key: string;
  /** 状态值 */
  value: unknown;
  /** 最近更新时间戳 */
  updatedAt: number;
  /** 过期时间戳（0 表示不过期） */
  expiresAt: number;
  /** 重试次数（对应文章"重试次数可见"要求） */
  retryCount: number;
  /** 关联 trace_id */
  traceId: string | null;
  /** 状态来源 */
  source: 'localStorage' | 'sessionStorage' | 'memory';
}

/** 状态变更 diff 条目 */
export interface StateDiff {
  /** 字段路径 */
  path: string;
  /** 旧值 */
  oldValue: unknown;
  /** 新值 */
  newValue: unknown;
  /** 变更类型 */
  kind: 'added' | 'removed' | 'updated';
}

/** 状态变更时间线条目 */
export interface StateTimelineEntry {
  /** 唯一 ID */
  id: string;
  /** 状态键名 */
  key: string;
  /** 变更时间戳 */
  timestamp: number;
  /** diff 列表 */
  diffs: StateDiff[];
  /** 关联 trace_id */
  traceId: string | null;
}
