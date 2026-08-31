/**
 * apiToken 单测（遗留修复：T4.2 FLASK_API_TOKEN 401）
 * - localStorage 持久化 / 清除 / 订阅通知 / authHeader 注入；
 * - apiClient.request() 在令牌存在时注入 Authorization: Bearer，无令牌时不注入。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { clearApiToken, getApiToken, setApiToken, subscribeApiToken, authHeader } from './apiToken';
import { request } from './apiClient';

const KEY = 'yunshu_api_token';

/**
 * 本环境的 jsdom localStorage 不完整（clear 缺失），显式 stub 一个
 * 完整 Storage 实现，确保测试与浏览器行为一致。
 */
function installStorage() {
  const store = new Map<string, string>();
  const storage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: () => null,
    get length() {
      return store.size;
    },
  };
  vi.stubGlobal('localStorage', storage);
}

beforeEach(() => {
  installStorage();
});

describe('apiToken 存储', () => {
  it('默认无令牌', () => {
    expect(getApiToken()).toBe('');
  });

  it('set 后持久化到 localStorage 并可读回（trim）', () => {
    setApiToken('  abc-123  ');
    expect(localStorage.getItem(KEY)).toBe('abc-123');
    expect(getApiToken()).toBe('abc-123');
  });

  it('clear 清除 localStorage', () => {
    setApiToken('abc');
    clearApiToken();
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(getApiToken()).toBe('');
  });

  it('set 空串等价于清除', () => {
    setApiToken('abc');
    setApiToken('');
    expect(getApiToken()).toBe('');
  });
});

describe('apiToken 订阅', () => {
  it('set/clear 通知订阅者（带最新值），返回取消订阅函数', () => {
    const seen: string[] = [];
    const unsub = subscribeApiToken((t) => seen.push(t));
    setApiToken('tok1');
    setApiToken('tok2');
    clearApiToken();
    unsub();
    setApiToken('tok3'); // 已取消订阅，不再通知
    expect(seen).toEqual(['tok1', 'tok2', '']);
  });
});

describe('authHeader', () => {
  it('无令牌返回空对象', () => {
    expect(authHeader()).toEqual({});
  });

  it('有令牌返回 Authorization: Bearer', () => {
    setApiToken('secret-token');
    expect(authHeader()).toEqual({ Authorization: 'Bearer secret-token' });
  });
});

describe('apiClient.request 令牌注入', () => {
  it('令牌存在时注入 Authorization: Bearer，且保留 Content-Type', async () => {
    setApiToken('tok-xyz');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    try {
      await request('/api/protected', { method: 'POST', body: { a: 1 } });
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe('/api/protected');
      expect(init.headers).toEqual({
        'Content-Type': 'application/json',
        Authorization: 'Bearer tok-xyz',
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('无令牌时不注入 Authorization', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    try {
      await request('/api/plugins');
      const [, init] = fetchMock.mock.calls[0];
      expect(init.headers).toEqual({});
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
