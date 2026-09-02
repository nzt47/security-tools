/**
 * WorkbenchChatPage 集成测试 —— 会话任务页「历史问话」侧滑面板入口
 * ------------------------------------------------
 * 验收点：会话页头部按钮可展开历史问话面板，面板按当前选中会话拉取 /api/history。
 * （面板内部的搜索/复制/删除/跳转交互由 HistoryDrawer.test.tsx 单独覆盖。）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import WorkbenchChatPage from './WorkbenchChatPage';
import { useLayoutStore } from '../stores/useLayoutStore';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

const HISTORY = [
  {
    user: '第一条历史问话',
    Yunshu: '回复一',
    mode: 'normal',
    timestamp: '2026-06-27T11:41:05.983168+08:00',
    _real_index: 0,
  },
  {
    user: '第二条历史问话',
    Yunshu: '回复二',
    mode: 'normal',
    timestamp: '2026-06-27T11:42:29.983168+08:00',
    _real_index: 1,
  },
];

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === '/api/sessions') {
    return jsonResponse({
      sessions: [{ id: 'sess-1', title: '会话 A' }],
      current_id: 'sess-1',
    });
  }
  if (url.startsWith('/api/sessions/')) return jsonResponse([]);
  if (url.startsWith('/api/history')) return jsonResponse(HISTORY);
  if (url.startsWith('/api/context/status')) return jsonResponse({});
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

describe('WorkbenchChatPage · 历史问话面板入口', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    resetStore();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    resetStore();
  });

  it('头部按钮展开历史问话侧滑面板，并按当前会话拉取历史', async () => {
    render(<WorkbenchChatPage />);

    // 头部入口按钮（页面始终渲染）
    const trigger = await screen.findByRole('button', { name: /历史问话/ });
    expect(trigger).toBeInTheDocument();

    // 点击 → 侧滑面板出现（dialog 语义 + 当前会话历史列表）
    fireEvent.click(trigger);
    expect(
      await screen.findByRole('dialog', { name: '历史问话' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('第一条历史问话')).toBeInTheDocument();
    expect(screen.getByText('第二条历史问话')).toBeInTheDocument();
    // 请求带 ?session= 当前会话（sess-1）
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/history?session=sess-1',
      expect.anything(),
    );
  });

  it('会话页不引入 legacy Chat 组件链（由导入面保证），头部仍渲染会话切换', async () => {
    render(<WorkbenchChatPage />);
    // 会话切换下拉存在（现有头部能力未破坏）
    expect(await screen.findByTitle('切换历史会话')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '会话 A' })).toBeInTheDocument();
  });
});
