/**
 * API 令牌管理（遗留修复：T4.2 结案报告的 FLASK_API_TOKEN 401 问题）
 *
 * 背景：后端 require_token 保护所有危险写端点（/api/plugins/reload、schema 提交
 * 端点等），令牌经 `Authorization: Bearer <token>` 或 `X-API-Token` 传递
 * （见 app_server.py require_token）。此前前端 apiClient 无任何令牌注入，
 * 一旦 .env 启用 FLASK_API_TOKEN，插件刷新 / 配置提交全部 401。
 *
 * 本模块：
 * - getApiToken / setApiToken / clearApiToken —— localStorage 持久化（隐私模式等
 *   异常静默降级为空令牌）；
 * - subscribeApiToken —— 令牌变化订阅（UI 可响应更新）；
 * - authHeader() —— 供 apiClient.request() 注入请求头（有令牌才返回）。
 *
 * 安全说明：令牌存 localStorage 与「浏览器本地工具」定位匹配（单人内部工具）；
 * 生产多用户环境应改为登录态/HttpOnly Cookie，此处不越权实现。
 */
const STORAGE_KEY = 'yunshu_api_token';

type TokenListener = (token: string) => void;

const listeners = new Set<TokenListener>();

/** 读取当前令牌（localStorage 不可用时返回空串） */
export function getApiToken(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

/** 保存令牌（trim 后落盘；空串 = 清除），并通知订阅者 */
export function setApiToken(token: string): void {
  const next = token.trim();
  try {
    if (next) localStorage.setItem(STORAGE_KEY, next);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* localStorage 不可用（隐私模式等）：仅内存订阅生效 */
  }
  listeners.forEach((l) => l(next));
}

/** 清除令牌 */
export function clearApiToken(): void {
  setApiToken('');
}

/** 订阅令牌变化；返回取消订阅函数 */
export function subscribeApiToken(listener: TokenListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 供 apiClient 注入的鉴权请求头；无令牌时返回空对象（不影响未保护端点） */
export function authHeader(): Record<string, string> {
  const token = getApiToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
