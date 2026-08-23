/**
 * 通用 API 客户端 — 统一 HTTP 请求层（{ok:true} 协议专用）
 *
 * 提取自 skillsApi.ts 的 request<T>() + ApiError 模式，供各模块复用。
 * 统一 AbortController 取消、业务错误码、JSON 解析与边界显性化。
 *
 * 不变量【不易】：
 * - 与 skillsApi.ts 的 request 行为对齐（AbortError 静默、网络错误抛出、HTTP !ok 抛出）
 * - 后端响应契约：{ ok: true, data... } 或 { ok: false, error: string }
 *
 * Why 与 @/utils/request 并存：后者针对 {code,data,message} 解包协议（axios），
 * 而知识库等路由返回 {ok:true} 裸协议，解包器会把 ok 误判为业务错误，故两套并存。
 */

const API_BASE = ''; // 同域，dev 模式下 vite proxy /api → 127.0.0.1:5678

// ═══════════════════════════════════════════════════════════════
//  错误类（边界显性化）
// ═══════════════════════════════════════════════════════════════

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

// ═══════════════════════════════════════════════════════════════
//  请求工具（AbortController + 业务错误码）
// ═══════════════════════════════════════════════════════════════

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  // 【Why】知识库路由挂 @require_token：注入当前登录 token，与 @/utils/request 拦截器口径一致
  const { STORAGE_KEYS, storage } = await import('@/utils/storage')
  const token = storage.getRaw(STORAGE_KEYS.TOKEN)
  if (token) headers['Authorization'] = `Bearer ${token}`

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') {
      throw new ApiError('API_REQUEST_ABORTED', '请求已取消', 0);
    }
    throw new ApiError(
      'API_NETWORK_ERROR',
      `网络请求失败: ${e instanceof Error ? e.message : String(e)}`,
      0,
    );
  }

  let payload: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!res.ok) {
    const p = (payload || {}) as Record<string, unknown>;
    throw new ApiError(
      (p.code as string) || 'API_HTTP_ERROR',
      (p.error as string) || `HTTP ${res.status}`,
      res.status,
      // 将完整错误 body 作为 details（后端错误字段如 incoming_links/violations 均在顶层）
      p,
    );
  }

  return payload as T;
}

// ═══════════════════════════════════════════════════════════════
//  debounce 工具（搜索框防抖）
// ═══════════════════════════════════════════════════════════════

export function debounce<T extends (...args: never[]) => void>(
  fn: T,
  wait: number,
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (...args: Parameters<T>) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

// ═══════════════════════════════════════════════════════════════
//  Query string 构造工具
// ═══════════════════════════════════════════════════════════════

export function buildQuery(params: Record<string, unknown>): string {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null) return;
    if (Array.isArray(v)) {
      v.forEach((x) => q.append(k, String(x)));
    } else {
      q.set(k, String(v));
    }
  });
  return q.toString();
}
