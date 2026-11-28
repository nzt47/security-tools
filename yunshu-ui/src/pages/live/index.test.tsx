/**
 * LiveShare 只读页测试：首屏分页 / 轮询游标增量 / 失效与网络错误提示。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LiveSharePage } from './index';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

let pages: Record<string, unknown>;
let failing = false;
const urls: string[] = [];

beforeEach(() => {
  failing = false;
  urls.length = 0;
  pages = {
    '0': { ok: true, title: '被分享的会话', messages: [
      { seq: 1, role: 'user', content: '第一问', chars: 3, truncated: false, timestamp: 't1' },
      { seq: 2, role: 'assistant', content: '第一答', chars: 3, truncated: false, timestamp: 't2' },
    ], total: 3, next_after: 2, truncated: true },
    '2': { ok: true, title: '被分享的会话', messages: [
      { seq: 3, role: 'user', content: '第二问', chars: 3, truncated: false, timestamp: 't3' },
    ], total: 3, next_after: 3, truncated: false },
    '3': { ok: true, title: '被分享的会话', messages: [], total: 3, next_after: 3, truncated: false },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    if (failing) throw new Error('network down');
    const after = new URL(url, 'http://x').searchParams.get('after') || '0';
    return jsonResponse(pages[after] ?? pages['3']);
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function renderPage(token = 'tok_abc') {
  return render(
    <MemoryRouter initialEntries={['/live/' + token]}>
      <Routes>
        <Route path="/live/:token" element={<LiveSharePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('LiveSharePage', () => {
  it('首屏渲染标题与消息，并带上分享令牌请求头', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('live-share-title').textContent).toContain('被分享的会话'));
    expect(screen.getAllByTestId('live-share-message')).toHaveLength(2);
    expect(urls[0]).toContain('/api/liveshare/messages?after=0');
    const headers = (fetch as unknown as { mock: { calls: [unknown, { headers?: Record<string, string> }][] } })
      .mock.calls[0][1]?.headers || {};
    expect(headers['X-LiveShare-Token']).toBe('tok_abc');
  });

  it('轮询按 next_after 增量拉取，不重复追加', async () => {
    // shouldAdvanceTime：让 testing-library 的 waitFor 内部计时器也跟着走，
    // 否则 fake timers 会把 waitFor 自己卡死（实测 5s 超时）
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('live-share-message')).toHaveLength(2));
    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(screen.getAllByTestId('live-share-message')).toHaveLength(3));
    expect(urls.some((u) => u.includes('after=2'))).toBe(true);
    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(screen.getAllByTestId('live-share-message')).toHaveLength(3));
  });

  it('404 显示同一句失效提示', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ok: false }, false, 404)));
    renderPage();
    await waitFor(() => expect(screen.getByTestId('live-share-invalid')).toBeTruthy());
    expect(screen.getByTestId('live-share-invalid').textContent).toContain('链接不存在或已失效');
  });

  it('网络错误给重试提示，不崩', async () => {
    failing = true;
    renderPage();
    await waitFor(() => expect(screen.getByTestId('live-share-error')).toBeTruthy());
    expect(screen.getByTestId('live-share-error').textContent).toContain('读取失败');
  });
});
