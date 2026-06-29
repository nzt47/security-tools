/**
 * DevConsole shared 工具函数单元测试
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  copyText,
  formatTime,
  formatDuration,
  statusBadgeClass,
  methodClass,
  durationClass,
  truncate,
} from './shared';

describe('shared 工具函数', () => {
  describe('formatTime', () => {
    it('格式化为 HH:mm:ss.SSS', () => {
      const ts = new Date(2026, 0, 1, 14, 30, 45, 123).getTime();
      const result = formatTime(ts);
      expect(result).toMatch(/\d{2}:\d{2}:\d{2}\.\d{3}/);
      expect(result).toBe('14:30:45.123');
    });
  });

  describe('formatDuration', () => {
    it('< 1ms 显示两位小数', () => {
      expect(formatDuration(0.5)).toBe('0.50ms');
    });

    it('1-999ms 显示整数', () => {
      expect(formatDuration(100)).toBe('100ms');
    });

    it('>= 1000ms 显示秒', () => {
      expect(formatDuration(1500)).toBe('1.50s');
    });
  });

  describe('statusBadgeClass', () => {
    it('0 → muted', () => {
      expect(statusBadgeClass(0)).toBe('badge-muted');
    });
    it('2xx → ok', () => {
      expect(statusBadgeClass(200)).toBe('badge-ok');
    });
    it('3xx → info', () => {
      expect(statusBadgeClass(302)).toBe('badge-info');
    });
    it('4xx → warn', () => {
      expect(statusBadgeClass(404)).toBe('badge-warn');
    });
    it('5xx → err', () => {
      expect(statusBadgeClass(500)).toBe('badge-err');
    });
  });

  describe('methodClass', () => {
    it('GET', () => expect(methodClass('GET')).toBe('method-get'));
    it('POST', () => expect(methodClass('POST')).toBe('method-post'));
    it('PUT', () => expect(methodClass('PUT')).toBe('method-put'));
    it('PATCH', () => expect(methodClass('PATCH')).toBe('method-put'));
    it('DELETE', () => expect(methodClass('DELETE')).toBe('method-delete'));
    it('其他', () => expect(methodClass('OPTIONS')).toBe('method-other'));
  });

  describe('durationClass', () => {
    it('< 16ms → ok', () => {
      expect(durationClass(10)).toBe('badge-ok');
    });
    it('16-100ms → warn', () => {
      expect(durationClass(50)).toBe('badge-warn');
    });
    it('> 100ms → err', () => {
      expect(durationClass(200)).toBe('badge-err');
    });
  });

  describe('truncate', () => {
    it('短文本不截断', () => {
      expect(truncate('hello', 80)).toBe('hello');
    });
    it('长文本截断并加省略号', () => {
      const long = 'a'.repeat(100);
      const result = truncate(long, 10);
      expect(result).toHaveLength(11);
      expect(result.endsWith('…')).toBe(true);
    });
  });

  describe('copyText', () => {
    beforeEach(() => {
      // mock clipboard API
      Object.assign(navigator, {
        clipboard: {
          writeText: vi.fn().mockResolvedValue(undefined),
        },
      });
      // jsdom 默认 isSecureContext=false，强制开启以走 clipboard 分支
      Object.defineProperty(window, 'isSecureContext', {
        value: true,
        configurable: true,
        writable: true,
      });
    });

    afterEach(() => {
      Object.defineProperty(window, 'isSecureContext', {
        value: false,
        configurable: true,
        writable: true,
      });
    });

    it('通过 clipboard API 复制成功', async () => {
      const ok = await copyText('test');
      expect(ok).toBe(true);
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('test');
    });

    it('clipboard 失败时回退到 execCommand', async () => {
      (navigator.clipboard.writeText as any).mockRejectedValue(
        new Error('denied')
      );
      // jsdom 无 execCommand，手动定义
      (document as any).execCommand = vi.fn().mockReturnValue(true);
      const ok = await copyText('fallback');
      expect(ok).toBe(true);
      expect(document.execCommand).toHaveBeenCalledWith('copy');
      delete (document as any).execCommand;
    });
  });
});
