/**
 * requestInterceptor.ts 单元测试
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installInterceptors,
  uninstallInterceptors,
  subscribeNetwork,
  subscribeError,
  parseTraceId,
  isInstalled,
  __clearListenersForTest,
} from './requestInterceptor';

describe('requestInterceptor 请求拦截器', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    __clearListenersForTest();
    // 重置 installed 状态
    uninstallInterceptors();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    uninstallInterceptors();
    __clearListenersForTest();
    vi.restoreAllMocks();
  });

  describe('parseTraceId', () => {
    it('从 traceparent 头解析 trace_id', () => {
      const headers = new Headers({
        traceparent: '00-abc123def456-span001-01',
      });
      expect(parseTraceId(headers)).toBe('abc123def456');
    });

    it('兼容 X-Trace-Id 头', () => {
      const headers = new Headers({ 'x-trace-id': 'xyz789' });
      expect(parseTraceId(headers)).toBe('xyz789');
    });

    it('traceparent 优先于 X-Trace-Id', () => {
      const headers = new Headers({
        traceparent: '00-priority-id-01',
        'x-trace-id': 'fallback',
      });
      expect(parseTraceId(headers)).toBe('priority');
    });

    it('无追踪头时返回 null', () => {
      const headers = new Headers({});
      expect(parseTraceId(headers)).toBeNull();
    });

    it('traceparent 格式异常时回退到 X-Trace-Id', () => {
      const headers = new Headers({
        traceparent: 'invalid',
        'x-trace-id': 'fallback-id',
      });
      expect(parseTraceId(headers)).toBe('fallback-id');
    });
  });

  describe('installInterceptors', () => {
    it('安装后 isInstalled 返回 true', () => {
      installInterceptors();
      expect(isInstalled()).toBe(true);
    });

    it('重复调用幂等（不重复安装）', () => {
      installInterceptors();
      installInterceptors();
      expect(isInstalled()).toBe(true);
    });

    it('返回卸载函数，调用后 isInstalled 为 false', () => {
      const uninstall = installInterceptors();
      expect(isInstalled()).toBe(true);
      uninstall();
      expect(isInstalled()).toBe(false);
    });
  });

  describe('fetch 劫持', () => {
    it('捕获成功请求的 URL/method/status/duration/trace_id', async () => {
      const mockResponse = new Response('{"ok":true}', {
        status: 200,
        headers: { traceparent: '00-traceabc-span001-01' },
      });
      globalThis.fetch = vi.fn().mockResolvedValue(mockResponse) as any;

      const records: any[] = [];
      subscribeNetwork((r) => records.push(r));

      installInterceptors();
      await fetch('/api/test', { method: 'POST', body: '{"k":"v"}' });

      expect(records).toHaveLength(1);
      const r = records[0];
      expect(r.url).toBe('/api/test');
      expect(r.method).toBe('POST');
      expect(r.status).toBe(200);
      expect(r.traceId).toBe('traceabc');
      expect(r.source).toBe('fetch');
      expect(r.duration).toBeGreaterThanOrEqual(0);
      expect(r.requestBody).toBe('{"k":"v"}');
    });

    it('捕获请求失败（网络错误）', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('network down')) as any;

      const records: any[] = [];
      subscribeNetwork((r) => records.push(r));

      installInterceptors();
      await expect(fetch('/api/fail')).rejects.toThrow('network down');

      expect(records).toHaveLength(1);
      expect(records[0].status).toBe(0);
      expect(records[0].error).toBe('network down');
    });

    it('卸载后恢复原始 fetch', async () => {
      const mockResponse = new Response('{}', { status: 200 });
      const mockFetch = vi.fn().mockResolvedValue(mockResponse) as any;
      globalThis.fetch = mockFetch;

      const uninstall = installInterceptors();
      uninstall();

      const records: any[] = [];
      subscribeNetwork((r) => records.push(r));

      await fetch('/api/test');
      expect(records).toHaveLength(0); // 卸载后不再捕获
    });
  });

  describe('XHR 劫持', () => {
    it('安装后 open/send 被替换', () => {
      const origOpen = XMLHttpRequest.prototype.open;
      const origSend = XMLHttpRequest.prototype.send;
      installInterceptors();
      expect(XMLHttpRequest.prototype.open).not.toBe(origOpen);
      expect(XMLHttpRequest.prototype.send).not.toBe(origSend);
    });

    it('卸载后恢复原始 open/send', () => {
      const origOpen = XMLHttpRequest.prototype.open;
      const origSend = XMLHttpRequest.prototype.send;
      const uninstall = installInterceptors();
      uninstall();
      expect(XMLHttpRequest.prototype.open).toBe(origOpen);
      expect(XMLHttpRequest.prototype.send).toBe(origSend);
    });

    it('XHR 请求 loadend 触发并记录 trace_id', () => {
      // mock 原始 send 避免真实网络请求
      vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(function (
        this: XMLHttpRequest
      ) {
        // 空实现：不真实发送，由测试手动 dispatch loadend
      });

      const records: any[] = [];
      subscribeNetwork((r) => records.push(r));

      installInterceptors();

      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/api/xhr-test');
      // mock 响应属性（loadend 回调读取）
      Object.defineProperty(xhr, 'status', { value: 200, configurable: true });
      Object.defineProperty(xhr, 'getAllResponseHeaders', {
        value: () => 'traceparent: 00-xhrtrace-span-01\r\n',
        configurable: true,
      });
      xhr.send();
      // 手动触发 loadend（真实 send 被 mock）
      xhr.dispatchEvent(new Event('loadend'));

      expect(records).toHaveLength(1);
      expect(records[0].url).toBe('/api/xhr-test');
      expect(records[0].method).toBe('GET');
      expect(records[0].status).toBe(200);
      expect(records[0].traceId).toBe('xhrtrace');
      expect(records[0].source).toBe('xhr');
    });

    it('XHR 网络错误（status=0）记录 error', () => {
      vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(function () {});

      const records: any[] = [];
      subscribeNetwork((r) => records.push(r));

      installInterceptors();

      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/api/fail');
      Object.defineProperty(xhr, 'status', { value: 0, configurable: true });
      Object.defineProperty(xhr, 'getAllResponseHeaders', {
        value: () => '',
        configurable: true,
      });
      xhr.send();
      xhr.dispatchEvent(new Event('loadend'));

      expect(records).toHaveLength(1);
      expect(records[0].status).toBe(0);
      expect(records[0].error).toBeTruthy();
    });

    it('XHR 无 traceparent 时 traceId 为 null', () => {
      vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(function () {});

      const records: any[] = [];
      subscribeNetwork((r) => records.push(r));

      installInterceptors();

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/no-trace');
      Object.defineProperty(xhr, 'status', { value: 201, configurable: true });
      Object.defineProperty(xhr, 'getAllResponseHeaders', {
        value: () => 'content-type: application/json\r\n',
        configurable: true,
      });
      xhr.send('{"k":"v"}');
      xhr.dispatchEvent(new Event('loadend'));

      expect(records).toHaveLength(1);
      expect(records[0].traceId).toBeNull();
      expect(records[0].requestBody).toBe('{"k":"v"}');
    });
  });

  describe('错误捕获', () => {
    it('捕获 window.onerror 事件', () => {
      const errors: any[] = [];
      subscribeError((e) => errors.push(e));

      installInterceptors();

      // 模拟 error 事件
      const errorEvent = new ErrorEvent('error', {
        message: 'test error',
        filename: 'test.js',
        lineno: 10,
        colno: 5,
        error: new Error('test error'),
      });
      window.dispatchEvent(errorEvent);

      expect(errors).toHaveLength(1);
      expect(errors[0].message).toBe('test error');
      expect(errors[0].type).toBe('onerror');
      expect(errors[0].source).toBe('test.js');
      expect(errors[0].lineno).toBe(10);
    });

    it('捕获 unhandledrejection 事件', () => {
      const errors: any[] = [];
      subscribeError((e) => errors.push(e));

      installInterceptors();

      // jsdom 无 PromiseRejectionEvent 构造函数，用带 reason 的事件替代
      const rejEvent = new Event('unhandledrejection');
      Object.defineProperty(rejEvent, 'reason', {
        value: new Error('promise fail'),
        writable: true,
        configurable: true,
      });
      window.dispatchEvent(rejEvent);

      expect(errors).toHaveLength(1);
      expect(errors[0].type).toBe('unhandledrejection');
      expect(errors[0].message).toBe('promise fail');
    });
  });

  describe('事件总线', () => {
    it('subscribeNetwork 返回取消订阅函数', async () => {
      const records: any[] = [];
      const unsub = subscribeNetwork((r) => records.push(r));

      globalThis.fetch = vi.fn().mockResolvedValue(new Response('{}')) as any;
      installInterceptors();

      await fetch('/a');
      expect(records).toHaveLength(1);

      unsub();
      await fetch('/b');
      expect(records).toHaveLength(1); // 取消订阅后不再接收
    });

    it('监听器内部抛错不影响其他监听器', async () => {
      const calls: number[] = [];
      subscribeNetwork(() => {
        calls.push(1);
        throw new Error('listener error');
      });
      subscribeNetwork(() => {
        calls.push(2);
      });

      globalThis.fetch = vi.fn().mockResolvedValue(new Response('{}')) as any;
      installInterceptors();

      await fetch('/x');
      // 两个监听器都被调用（即使第一个抛错）
      expect(calls).toContain(1);
      expect(calls).toContain(2);
    });
  });
});
