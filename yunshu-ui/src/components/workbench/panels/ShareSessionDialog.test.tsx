/**
 * ShareSessionDialog 测试：状态读取 / 创建后只显示一次链接 / 复制 / 撤销 / 未启用如实提示。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ShareSessionDialog, shareLink } from './ShareSessionDialog';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

let statusPayload: Record<string, unknown>;
let createPayload: Record<string, unknown>;
let calls: { method: string; url: string; body?: unknown }[];

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = (init?.method as string) || 'GET';
  calls.push({ method, url, body: init?.body });
  if (url.endsWith('/share')) {
    if (method === 'POST') return jsonResponse(createPayload);
    if (method === 'DELETE') return jsonResponse({ ok: true, revoked: 1 });
    return jsonResponse(statusPayload);
  }
  return jsonResponse({ ok: false, error: 'unexpected' }, false, 500);
});

beforeEach(() => {
  statusPayload = { ok: true, enabled: true, shares: [
    { created_at: '2026-11-28T10:00:00Z', expires_at: '2026-11-29T10:00:00Z', revoked: false, active: true },
  ], default_ttl_seconds: 86400 };
  createPayload = { ok: true, token: 'tok_abc123', url_path: '/#/live/tok_abc123',
                    expires_at: '2026-11-29T10:00:00Z', ttl_seconds: 86400 };
  calls = [];
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockClear();
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ShareSessionDialog', () => {
  it('打开即读状态：显示生效中的分享数', async () => {
    render(<ShareSessionDialog open onClose={() => {}} sessionId="sess_1" />);
    await waitFor(() => expect(screen.getByTestId('session-share-count')).toBeTruthy());
    expect(screen.getByTestId('session-share-count').textContent).toContain('1');
    expect(calls[0].url).toBe('/api/sessions/sess_1/share');
    expect(calls[0].method).toBe('GET');
  });

  it('创建分享：回一次性链接并可复制', async () => {
    render(<ShareSessionDialog open onClose={() => {}} sessionId="sess_1" />);
    await waitFor(() => expect(screen.getByTestId('session-share-create')).toBeTruthy());
    fireEvent.click(screen.getByTestId('session-share-create'));
    await waitFor(() => expect(screen.getByTestId('session-share-link')).toBeTruthy());
    const input = screen.getByTestId('session-share-link') as HTMLInputElement;
    expect(input.value).toBe(shareLink('tok_abc123'));
    fireEvent.click(screen.getByTestId('session-share-copy'));
    await waitFor(() => expect(screen.getByTestId('session-share-copy').textContent).toContain('已复制'));
    const post = calls.find((c) => c.method === 'POST');
    expect(post?.body).toContain('86400');
  });

  it('撤销：调用 DELETE 并清掉一次性链接', async () => {
    render(<ShareSessionDialog open onClose={() => {}} sessionId="sess_1" />);
    await waitFor(() => expect(screen.getByTestId('session-share-revoke')).toBeTruthy());
    fireEvent.click(screen.getByTestId('session-share-create'));
    await waitFor(() => expect(screen.getByTestId('session-share-link')).toBeTruthy());
    fireEvent.click(screen.getByTestId('session-share-revoke'));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('session-share-link')).toBeNull());
  });

  it('开关关：如实显示未启用，不给创建入口', async () => {
    statusPayload = { ok: false, code: 'disabled', error: '会话分享未启用（CP_LIVESHARE_ENABLED=0）' };
    render(<ShareSessionDialog open onClose={() => {}} sessionId="sess_1" />);
    await waitFor(() => expect(screen.getByTestId('session-share-disabled')).toBeTruthy());
    expect(screen.queryByTestId('session-share-create')).toBeNull();
  });
});
