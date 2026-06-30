/**
 * App.tsx 埋点验证测试
 *
 * 验证目标：loadMessages 在遇到非 404 HTTP 错误（如 500）时，
 * trackEvent 是否被正确调用并记录 success: false。
 *
 * 状态同步机制说明：
 * - 使用 vi.mock 替换 trackEvent 为 spy，拦截调用并记录参数
 * - 使用 mockFetch 模拟 HTTP 500/403 响应，验证失败埋点路径
 * - trackEvent 自身有 try/catch 保护，埋点失败不影响主流程
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import type { Mock } from 'vitest';

// ── 模块 mock ──────────────────────────────────────────────

// mock useChatStream，避免实际 SSE 连接
vi.mock('./hooks/useChatStream', () => ({
  useChatStream: () => ({
    state: {
      streaming: false,
      reasoning: '',
      toolSteps: [],
      text: '',
      error: null,
      thinkingLabel: '',
    },
    send: vi.fn(),
    reset: vi.fn(),
  }),
}));

// mock Mascot 组件，避免 canvas 渲染
vi.mock('./components/Mascot', () => ({
  Mascot: () => null,
}));

// mock ChatWindow 组件，避免复杂子树
vi.mock('./components/Chat', () => ({
  ChatWindow: () => null,
  Message: {},
}));

// mock StatusIndicator / ToastContainer
vi.mock('./components/Status', () => ({
  StatusIndicator: () => null,
  ToastContainer: () => null,
}));

// mock observability 模块：保留 TrackEventName 真实值，替换 trackEvent 为 spy
// 使用 vi.hoisted 确保 mock 函数在 vi.mock 提升前已定义
const { trackEventMock } = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
}));
vi.mock('./config/observability', async (importActual) => {
  const actual = await importActual<typeof import('./config/observability')>();
  return {
    ...actual,
    trackEvent: trackEventMock,
  };
});

// ── 导入被测模块（在 mock 之后） ──────────────────────────
import App from './App';
import { TrackEventName } from './config/observability';

describe('App.tsx 埋点验证 — loadMessages HTTP 错误', () => {
  let fetchMock: Mock;

  beforeEach(() => {
    trackEventMock.mockClear();

    // ── localStorage mock ──────────────────────────────────
    // 解决 jsdom 环境下 localStorage.getItem is not a function 问题
    // App.tsx 第 31 行会在 useEffect 中调用 localStorage.getItem('yunshu_session_id')
    const localStorageStore: Record<string, string> = {};
    const localStorageMock = {
      getItem: vi.fn((key: string) => localStorageStore[key] ?? null),
      setItem: vi.fn((key: string, value: string) => {
        localStorageStore[key] = String(value);
      }),
      removeItem: vi.fn((key: string) => {
        delete localStorageStore[key];
      }),
      clear: vi.fn(() => {
        for (const k of Object.keys(localStorageStore)) delete localStorageStore[k];
      }),
      key: vi.fn((index: number) => Object.keys(localStorageStore)[index] ?? null),
      get length() {
        return Object.keys(localStorageStore).length;
      },
    };
    vi.stubGlobal('localStorage', localStorageMock);

    // 默认 mock：sessions 接口返回空列表，health 返回 ok
    fetchMock = vi.fn((url: string) => {
      if (url.includes('/api/sessions') && !url.includes('/messages')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ sessions: [], current_id: null }),
        } as Response);
      }
      if (url.includes('/api/health')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      if (url.includes('/api/status')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        json: () => Promise.resolve({}),
      } as Response);
    });
    global.fetch = fetchMock as any;

    // 安全清空 localStorage（某些测试环境可能不支持 clear）
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it('HTTP 500 错误时，trackEvent 应记录 success=false 和 http_status=500', async () => {
    // 模拟 /api/sessions/{sid}/messages 返回 500
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/api/sessions') && url.includes('/messages')) {
        return Promise.resolve({
          ok: false,
          status: 500,
          statusText: 'Internal Server Error',
          json: () => Promise.resolve({ error: '服务器内部错误' }),
        } as Response);
      }
      if (url.includes('/api/health')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      if (url.includes('/api/status')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      // sessions 列表返回一个会话，触发 loadMessages
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            sessions: [{ id: 'test-sess-500', title: '测试会话' }],
            current_id: 'test-sess-500',
          }),
      } as Response);
    });

    render(<App />);

    // 等待 trackEvent 被调用（loadMessages 在 useEffect 中异步执行）
    await waitFor(
      () => {
        expect(trackEventMock).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );

    // 查找 loadMessages 失败的埋点调用
    const failedCalls = trackEventMock.mock.calls.filter(
      ([eventName, payload]: [string, any]) =>
        eventName === TrackEventName.DASHBOARD_LOAD &&
        payload.module === 'messages' &&
        payload.success === false,
    );

    expect(failedCalls.length).toBeGreaterThan(0);
    expect(failedCalls[0][1].http_status).toBe(500);
    expect(failedCalls[0][1].duration_ms).toBeGreaterThanOrEqual(0);
  });

  it('HTTP 403 错误时，trackEvent 也应记录 success=false 和 http_status=403', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/api/sessions') && url.includes('/messages')) {
        return Promise.resolve({
          ok: false,
          status: 403,
          statusText: 'Forbidden',
          json: () => Promise.resolve({ error: '无权限' }),
        } as Response);
      }
      if (url.includes('/api/health')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      if (url.includes('/api/status')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            sessions: [{ id: 'test-sess-403', title: '测试会话' }],
            current_id: 'test-sess-403',
          }),
      } as Response);
    });

    render(<App />);

    await waitFor(
      () => {
        expect(trackEventMock).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );

    const failedCalls = trackEventMock.mock.calls.filter(
      ([eventName, payload]: [string, any]) =>
        eventName === TrackEventName.DASHBOARD_LOAD &&
        payload.module === 'messages' &&
        payload.success === false &&
        payload.http_status === 403,
    );

    expect(failedCalls.length).toBeGreaterThan(0);
  });

  it('HTTP 200 成功时，trackEvent 应记录 success=true', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/api/sessions') && url.includes('/messages')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              { role: 'user', content: '你好', timestamp: Date.now() },
              { role: 'assistant', content: '你好！', timestamp: Date.now() },
            ]),
        } as Response);
      }
      if (url.includes('/api/health')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      if (url.includes('/api/status')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            sessions: [{ id: 'test-sess-ok', title: '测试会话' }],
            current_id: 'test-sess-ok',
          }),
      } as Response);
    });

    render(<App />);

    await waitFor(
      () => {
        expect(trackEventMock).toHaveBeenCalled();
      },
      { timeout: 3000 },
    );

    const successCalls = trackEventMock.mock.calls.filter(
      ([eventName, payload]: [string, any]) =>
        eventName === TrackEventName.DASHBOARD_LOAD &&
        payload.module === 'messages' &&
        payload.success === true,
    );

    expect(successCalls.length).toBeGreaterThan(0);
    // 成功时不应有 http_status 字段
    expect(successCalls[0][1].http_status).toBeUndefined();
  });
});
