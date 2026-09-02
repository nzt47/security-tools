/**
 * HistoryDrawer 单元测试 —— 会话任务页历史问话侧滑面板
 * ------------------------------------------------
 * 覆盖验收交互：展开加载 / 搜索 / 复制 / 删除 / 跳转定位 / Escape 关闭 / 降级提示。
 * 通过桩全局 fetch 模拟后端（GET /api/history、DELETE /api/history/{idx}、
 * GET /api/sessions/{id}/messages），store 使用内存 localStorage。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HistoryDrawer } from './HistoryDrawer';
import { useLayoutStore } from '../../../stores/useLayoutStore';

/** 构造 fetch 假响应（historyApi.request 消费 ok/status/text） */
function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

interface FakeEntry {
  user: string;
  Yunshu: string;
  mode: string;
  timestamp: string;
  _real_index: number;
}

const LONG_QUESTION =
  '这是一条超过 64 个字符的历史问话，用于验证复制动作写入剪贴板的是完整原文而不是截断预览，请复制我全部内容';

const ENTRIES: FakeEntry[] = [
  {
    user: '第一条历史问话',
    Yunshu: '回复一',
    mode: 'normal',
    timestamp: '2026-06-27T11:41:05.983168+08:00',
    _real_index: 0,
  },
  {
    user: LONG_QUESTION,
    Yunshu: '回复二',
    mode: 'normal',
    timestamp: '2026-06-27T11:42:29.983168+08:00',
    _real_index: 1,
  },
];

/** 会话消息（user 消息序 = 历史窗口序，j↔第 j 条 user 消息） */
const SESSION_MESSAGES = [
  { role: 'user', content: '第一条历史问话', timestamp: '2026-06-27T11:41:05+08:00' },
  { role: 'assistant', content: '回复一', timestamp: '2026-06-27T11:41:06+08:00' },
  { role: 'user', content: LONG_QUESTION, timestamp: '2026-06-27T11:42:29+08:00' },
  { role: 'assistant', content: '回复二', timestamp: '2026-06-27T11:42:30+08:00' },
];

let serverEntries: FakeEntry[];

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = (init?.method as string) || 'GET';
  if (method === 'GET' && url.startsWith('/api/history')) {
    return jsonResponse(serverEntries);
  }
  if (method === 'DELETE' && url.startsWith('/api/history/')) {
    const match = url.match(/\/api\/history\/(\d+)/);
    if (match) {
      const idx = Number(match[1]);
      serverEntries = serverEntries.filter((e) => e._real_index !== idx);
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: false, error: '索引超出范围' }, false, 404);
  }
  if (method === 'GET' && url.startsWith('/api/sessions/')) {
    // 降级场景：empty-session 无可加载消息
    if (url.includes('empty-session')) return jsonResponse([]);
    return jsonResponse(SESSION_MESSAGES);
  }
  return jsonResponse({});
});

function resetStore() {
  useLayoutStore.setState({
    messages: [],
    thinking: [],
    streaming: false,
    activeStreamId: null,
    highlightMsgId: null,
  });
}

function renderDrawer(sessionId = 's1') {
  const onClose = vi.fn();
  const view = render(<HistoryDrawer sessionId={sessionId} onClose={onClose} />);
  return { onClose, ...view };
}

describe('HistoryDrawer 历史问话侧滑面板', () => {
  beforeEach(() => {
    serverEntries = ENTRIES.map((e) => ({ ...e }));
    vi.stubGlobal('fetch', fetchMock);
    resetStore();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    resetStore();
  });

  it('展开加载：渲染当前会话的历史问话，最新在上', async () => {
    renderDrawer('s1');
    // 窗口序为旧→新，展示序 reverse → "第二条"（长问话）出现在第一条之上
    expect(await screen.findByText(/这是一条超过 64 个字符/)).toBeInTheDocument();
    expect(screen.getByText('第一条历史问话')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/history?session=s1',
      expect.objectContaining({}),
    );
    // 计数副标题
    expect(screen.getByText(/2 条 · 当前会话/)).toBeInTheDocument();
  });

  it('搜索：按用户问话内容过滤，无匹配时展示空态', async () => {
    renderDrawer('s1');
    await screen.findByText(/这是一条超过 64 个字符/);
    const input = screen.getByPlaceholderText('搜索问话…');
    fireEvent.change(input, { target: { value: '第一条' } });
    expect(screen.getByText('第一条历史问话')).toBeInTheDocument();
    expect(screen.queryByText(/这是一条超过 64 个字符/)).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: '不存在的问话' } });
    expect(screen.getByText('无匹配问话')).toBeInTheDocument();
    // 清空搜索恢复全量
    fireEvent.change(input, { target: { value: '' } });
    expect(await screen.findByText(/这是一条超过 64 个字符/)).toBeInTheDocument();
  });

  it('复制：向剪贴板写入完整原文（含超长截断预览条目）', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    renderDrawer('s1');
    await screen.findByText(/这是一条超过 64 个字符/);
    const copyButtons = screen.getAllByTitle('复制');
    // 最新在上：第一条为长问话
    fireEvent.click(copyButtons[0]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LONG_QUESTION));
    delete (navigator as { clipboard?: unknown }).clipboard;
  });

  it('删除：确认后调用 DELETE /api/history/{index}?session=，并刷新列表', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderDrawer('s1');
    await screen.findByText(/这是一条超过 64 个字符/);
    const delButtons = screen.getAllByTitle('删除此条');
    // 展示序为最新在上 → 第 0 条即长问话（_real_index=1）
    fireEvent.click(delButtons[0]);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/history/1?session=s1',
        expect.objectContaining({ method: 'DELETE' }),
      ),
    );
    // 列表刷新后该条消失
    await waitFor(() =>
      expect(screen.queryByText(/这是一条超过 64 个字符/)).not.toBeInTheDocument(),
    );
    expect(screen.getByText('第一条历史问话')).toBeInTheDocument();
    // 删除需用户确认
    expect(window.confirm).toHaveBeenCalled();
  });

  it('删除：用户取消确认时不发 DELETE 请求', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderDrawer('s1');
    await screen.findByText(/这是一条超过 64 个字符/);
    fireEvent.click(screen.getAllByTitle('删除此条')[0]);
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('/api/history/'),
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('跳转定位：点击条目定位到消息流中对应 user 消息（store.highlightMsgId）并关闭', async () => {
    // 预置会话消息：与历史窗口同序（user 消息 ordinal 0/1）
    useLayoutStore.setState({
      messages: SESSION_MESSAGES.map((m, i) => ({
        id: `hist-s1-${i}`,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        createdAt: Date.now(),
        status: 'done' as const,
      })),
    });
    const { onClose } = renderDrawer('s1');
    await screen.findByText(/这是一条超过 64 个字符/);
    // 点击最新一条（长问话，窗口 ordinal=1）→ 定位 hist-s1-2
    fireEvent.click(screen.getByText(/这是一条超过 64 个字符/));
    await waitFor(() =>
      expect(useLayoutStore.getState().highlightMsgId).toBe('hist-s1-2'),
    );
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('跳转降级：消息流为空且会话无可加载消息时提示并保持面板', async () => {
    // messages 空 → 触发 loadSessionHistory('/api/sessions/s1/messages')，
    // 该 URL mock 也会返回 SESSION_MESSAGES → 定位成功。改用一个特殊会话验证降级。
    const { onClose } = renderDrawer('empty-session');
    await screen.findByText(/这是一条超过 64 个字符/);
    fireEvent.click(screen.getByText('第一条历史问话'));
    await waitFor(() =>
      expect(
        screen.getByText(/未在会话中找到对应消息/),
      ).toBeInTheDocument(),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Escape 键关闭面板', async () => {
    const { onClose } = renderDrawer('s1');
    await screen.findByText(/这是一条超过 64 个字符/);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
