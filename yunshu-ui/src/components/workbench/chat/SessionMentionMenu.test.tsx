/**
 * # 会话引用（E3-3 尾巴）：输入 #关键词 → 列出会话 → 选中插入带 session_id 的引用行。
 *
 * 钉住：
 *  1. 只取末尾 #片段（与 @ 文件引用互不干扰）；
 *  2. 选中后插的是**模型可消费**的一行（title + session_id），不是 UI 链接；
 *  3. 无匹配/读不到列表都如实提示，可继续手打。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { MessageInput } from './MessageInput';
import { applySessionMention, extractSessionQuery, sessionMentionLine } from './SessionMentionMenu';

function mockFetch(body: unknown, ok = true) {
  const fn = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  }) as unknown as Response);
  vi.stubGlobal('fetch', fn);
  return fn;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('session mention 文本工具', () => {
  it('取末尾 # 片段', () => {
    expect(extractSessionQuery('看看 #E2')).toBe('E2');
    expect(extractSessionQuery('看看 #E2 收口')).toBeNull();
  });

  it('引用行带 session_id', () => {
    expect(sessionMentionLine('E2 收口', 's-123')).toBe('【引用会话】E2 收口（session_id=s-123）');
    expect(sessionMentionLine('', 's-1')).toContain('(未命名)');
  });

  it('替换时不留残字', () => {
    expect(applySessionMention('看看 #E2', 'E2 收口', 's-1')).toBe(
      '看看 【引用会话】E2 收口（session_id=s-1） ',
    );
  });
});

describe('MessageInput · # 会话菜单', () => {
  it('输入 #关键词 会拉会话列表并过滤', async () => {
    const fetchMock = mockFetch({
      sessions: [
        { id: 's-1', title: 'E2 收口' },
        { id: 's-2', title: 'E3 计划' },
      ],
      current_id: 's-1',
    });
    render(<MessageInput value="看看 #E2" onChange={() => {}} onSend={() => {}} />);
    await waitFor(() => expect(screen.getByText('E2 收口')).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByText('E3 计划')).toBeNull();
    const url = String((fetchMock.mock.calls[0] as unknown as [string])[0]);
    expect(url).toContain('/api/sessions');
  });

  it('选中后回传带 session_id 的引用行', async () => {
    mockFetch({ sessions: [{ id: 's-1', title: 'E2 收口' }] });
    const onChange = vi.fn();
    render(<MessageInput value="看看 #E2" onChange={onChange} onSend={() => {}} />);
    await waitFor(() => expect(screen.getByText('E2 收口')).toBeTruthy(), { timeout: 3000 });
    fireEvent.click(screen.getByText('E2 收口'));
    expect(onChange).toHaveBeenCalledWith('看看 【引用会话】E2 收口（session_id=s-1） ');
  });

  it('无匹配如实提示', async () => {
    mockFetch({ sessions: [] });
    render(<MessageInput value="#不存在的会话" onChange={() => {}} onSend={() => {}} />);
    await waitFor(() => expect(screen.getByText(/无匹配会话/)).toBeTruthy(), { timeout: 3000 });
  });
});
