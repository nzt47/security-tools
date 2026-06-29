/**
 * @sentry/react 未安装时的兜底 stub 模块
 *
 * 用途：当 @sentry/react 包未安装时，通过 vite.config.ts 的 resolve.alias
 *      将 '@sentry/react' 映射到本文件，保证 dev server 能正常启动。
 *
 * 设计原则：
 * - 所有 API 为空操作，调用安全失败（返回 undefined/null）
 * - 不抛异常，确保 sentry.ts 内部的 try/catch 不会触发
 * - 类型与 @sentry/react 兼容（导出 BrowserOptions 类型）
 *
 * 注意：本文件仅用于 dev 环境兜底，生产环境应安装 @sentry/react 或
 *      通过 tree-shaking 移除 sentry 相关代码。
 */

// ─── 类型定义（与 @sentry/react 兼容） ──────────────────────────────────

export interface BrowserOptions {
  dsn?: string;
  environment?: string;
  sampleRate?: number;
  tracesSampleRate?: number;
  release?: string;
  serverName?: string;
  integrations?: unknown[];
  maxBreadcrumbs?: number;
  attachStacktrace?: boolean;
  sendDefaultPii?: boolean;
  beforeSend?: (event: unknown) => unknown;
}

// ─── 空操作 API（全部 no-op） ──────────────────────────────────────────

export function init(_options: BrowserOptions): void {
  // stub: 不做任何事
}

export function reactRouterV6BrowserTracingIntegration(): unknown {
  return undefined;
}

export function browserTracingIntegration(): unknown {
  return undefined;
}

export function addBreadcrumb(_breadcrumb: unknown): void {
  // stub
}

export function setTag(_key: string, _value: unknown): void {
  // stub
}

export function setUser(_user: unknown): void {
  // stub
}

export function setContext(_name: string, _context: unknown): void {
  // stub
}

export function captureException(_error: unknown): string | null {
  return null;
}

export function captureMessage(_message: string, _level?: string): string | null {
  return null;
}

// 默认导出（兼容 `import * as Sentry` 形式）
const Sentry = {
  init,
  reactRouterV6BrowserTracingIntegration,
  browserTracingIntegration,
  addBreadcrumb,
  setTag,
  setUser,
  setContext,
  captureException,
  captureMessage,
};

export default Sentry;
