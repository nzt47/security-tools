/**
 * 逐条反馈（E3-3）：点赞 / 点踩的消息级投票
 *
 * 钉住四件事：
 *  1. 打的是既有 /api/feedback/submit（不新建路由，L5 直接吃到 like/dislike）；
 *  2. 请求体带 trace_id = 消息 id + context.source=chat_message（反馈中心可区分来源）；
 *  3. 失败必须回滚（不留「已投」的假象）；再点一次 = 取消（只清本地，不假装撤销服务端）；
 *  4. 已选态落 localStorage（刷新后仍是已选）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { MessageFeedback } from './MessageFeedback';

const MSG = 'm-abc123';

function mockFetch(ok = true) {
  const fn = vi.fn(async () => ({ ok, status: ok ? 200 : 500 }) as unknown as Response);
  vi.stubGlobal('fetch', fn);
  return fn;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('MessageFeedback · 逐条反馈', () => {
  it('点赞会 POST 到 /api/feedback/submit，且带消息 id 与来源', async () => {
    const fetchMock = mockFetch();
    render(<MessageFeedback messageId={MSG} sessionId="s-1" />);
    fireEvent.click(screen.getByLabelText('这条回复有帮助'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/feedback/submit');
    const body = JSON.parse(String(init.body));
    expect(body.trace_id).toBe(MSG);
    expect(body.feedback_type).toBe('like');
    expect(body.session_id).toBe('s-1');
    expect(body.context.source).toBe('chat_message');
    expect(body.context.message_id).toBe(MSG);
    await waitFor(() => expect(screen.getByLabelText('这条回复有帮助').getAttribute('aria-pressed')).toBe('true'));
  });

  it('点踩切换会带 dislike（同一消息不重复计票）', async () => {
    const fetchMock = mockFetch();
    render(<MessageFeedback messageId={MSG} />);
    fireEvent.click(screen.getByLabelText('这条回复不对'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body)).feedback_type).toBe('dislike');
    await waitFor(() => expect(screen.getByLabelText('这条回复不对').getAttribute('aria-pressed')).toBe('true'));
  });

  it('再点一次 = 取消，不再打后端', async () => {
    const fetchMock = mockFetch();
    render(<MessageFeedback messageId={MSG} />);
    fireEvent.click(screen.getByLabelText('这条回复有帮助'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByLabelText('这条回复有帮助'));
    await waitFor(() => expect(screen.getByLabelText('这条回复有帮助').getAttribute('aria-pressed')).toBe('false'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('yunshu.msg.feedback.' + MSG)).toBeNull();
  });

  it('提交失败要回滚并给提示', async () => {
    mockFetch(false);
    render(<MessageFeedback messageId={MSG} />);
    fireEvent.click(screen.getByLabelText('这条回复有帮助'));
    await waitFor(() => expect(screen.getByText('提交失败')).toBeTruthy());
    expect(screen.getByLabelText('这条回复有帮助').getAttribute('aria-pressed')).toBe('false');
    expect(localStorage.getItem('yunshu.msg.feedback.' + MSG)).toBeNull();
  });

  it('刷新后已选态保留（localStorage）', async () => {
    mockFetch();
    const first = render(<MessageFeedback messageId={MSG} />);
    fireEvent.click(screen.getByLabelText('这条回复不对'));
    await waitFor(() => expect(screen.getByLabelText('这条回复不对').getAttribute('aria-pressed')).toBe('true'));
    first.unmount();
    render(<MessageFeedback messageId={MSG} />);
    expect(screen.getByLabelText('这条回复不对').getAttribute('aria-pressed')).toBe('true');
  });
});
