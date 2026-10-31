/**
 * apiClient 错误面回归（2026-11-01）
 * ------------------------------------------------------------------
 * 用户实测：治理面板点「摘除来源 / 熔炉开关」只看到一句 `HTTP 403`，看不出原因。
 * 根因：`/api/cp/*` 的错误体是 `{code, message, decision}`（没有 `error` 字段），
 * 而 apiClient 只读 `error` ⇒ 回退成 "HTTP 403"，把「缺 reason / 缺二次认证」吞掉。
 * 这里钉死：**有 message 优先用 message**，没有才回退 error / 状态码。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, request } from './apiClient';

function mockResponse(body: unknown, status: number) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe('apiClient 错误信息', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('治理端点 {code,message} 的 message 要原样抛给 UI（不再只显示 HTTP 403）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse({
      code: 'panel_denied',
      message: '回滚 必须先填写 reason（不可逆动作，审计要求）',
      decision: { allowed: false, reason: 'x' },
    }, 403)));

    await expect(request('/api/cp/actions/rollback', { method: 'POST', body: {} }))
      .rejects.toMatchObject({
        name: 'ApiError',
        status: 403,
        code: 'panel_denied',
        message: '回滚 必须先填写 reason（不可逆动作，审计要求）',
      });
  });

  it('通用信封 {ok:false,error} 仍然可用（不回退）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse({ ok: false, error: '技能不存在' }, 404)));
    const err = (await request('/api/x').catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe('技能不存在');
  });

  it('两者都没有时才回退 HTTP 状态码', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse({}, 500)));
    const err = (await request('/api/x').catch((e) => e)) as ApiError;
    expect(err.message).toBe('HTTP 500');
  });
});
