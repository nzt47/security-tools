/**
 * DevConsole store 单元测试
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  useDevConsoleStore,
  trackPerf,
  measureRender,
} from './store';
import type { NetworkRecord, ErrorRecord } from './types';

function makeNetworkRecord(id: string): NetworkRecord {
  return {
    id,
    url: `/api/${id}`,
    method: 'GET',
    status: 200,
    duration: 10,
    traceId: null,
    timestamp: Date.now(),
    source: 'fetch',
  };
}

function makeErrorRecord(id: string): ErrorRecord {
  return {
    id,
    type: 'onerror',
    message: `err-${id}`,
    stack: '',
    traceId: null,
    timestamp: Date.now(),
  };
}

describe('DevConsole store', () => {
  beforeEach(() => {
    useDevConsoleStore.setState({
      networkRecords: [],
      errorRecords: [],
      perfRecords: [],
      paused: false,
      networkFilter: '',
    });
  });

  describe('addNetworkRecord', () => {
    it('追加网络记录', () => {
      useDevConsoleStore.getState().addNetworkRecord(makeNetworkRecord('1'));
      expect(useDevConsoleStore.getState().networkRecords).toHaveLength(1);
    });

    it('暂停期间丢弃记录', () => {
      useDevConsoleStore.getState().togglePause();
      useDevConsoleStore.getState().addNetworkRecord(makeNetworkRecord('1'));
      expect(useDevConsoleStore.getState().networkRecords).toHaveLength(0);
    });

    it('LRU 淘汰：超过 200 条移除最旧', () => {
      for (let i = 0; i < 202; i++) {
        useDevConsoleStore.getState().addNetworkRecord(makeNetworkRecord(`${i}`));
      }
      const records = useDevConsoleStore.getState().networkRecords;
      expect(records).toHaveLength(200);
      // 最旧的 2 条被淘汰，首条应为 id=2
      expect(records[0].id).toBe('2');
      expect(records[199].id).toBe('201');
    });
  });

  describe('addErrorRecord', () => {
    it('追加错误记录', () => {
      useDevConsoleStore.getState().addErrorRecord(makeErrorRecord('1'));
      expect(useDevConsoleStore.getState().errorRecords).toHaveLength(1);
    });

    it('暂停期间丢弃错误记录', () => {
      useDevConsoleStore.getState().togglePause();
      useDevConsoleStore.getState().addErrorRecord(makeErrorRecord('1'));
      expect(useDevConsoleStore.getState().errorRecords).toHaveLength(0);
    });
  });

  describe('addPerfRecord / trackPerf / measureRender', () => {
    it('trackPerf 追加性能记录', () => {
      trackPerf('render', 5);
      expect(useDevConsoleStore.getState().perfRecords).toHaveLength(1);
      expect(useDevConsoleStore.getState().perfRecords[0].name).toBe('render');
    });

    it('measureRender 测量并记录函数耗时', () => {
      const result = measureRender('compute', () => 42);
      expect(result).toBe(42);
      expect(useDevConsoleStore.getState().perfRecords).toHaveLength(1);
      expect(useDevConsoleStore.getState().perfRecords[0].duration).toBeGreaterThanOrEqual(0);
    });

    it('trackPerf 埋点失败不影响主流程', () => {
      // 强制 store 抛错（通过 spy）
      const spy = vi.spyOn(useDevConsoleStore.getState(), 'addPerfRecord');
      spy.mockImplementation(() => {
        throw new Error('store error');
      });
      // 不应抛错
      expect(() => trackPerf('x', 1)).not.toThrow();
      spy.mockRestore();
    });
  });

  describe('清空操作', () => {
    it('clearNetwork 仅清空网络记录', () => {
      useDevConsoleStore.getState().addNetworkRecord(makeNetworkRecord('1'));
      useDevConsoleStore.getState().addErrorRecord(makeErrorRecord('1'));
      useDevConsoleStore.getState().clearNetwork();
      expect(useDevConsoleStore.getState().networkRecords).toHaveLength(0);
      expect(useDevConsoleStore.getState().errorRecords).toHaveLength(1);
    });

    it('clearError 仅清空错误记录', () => {
      useDevConsoleStore.getState().addNetworkRecord(makeNetworkRecord('1'));
      useDevConsoleStore.getState().addErrorRecord(makeErrorRecord('1'));
      useDevConsoleStore.getState().clearError();
      expect(useDevConsoleStore.getState().errorRecords).toHaveLength(0);
      expect(useDevConsoleStore.getState().networkRecords).toHaveLength(1);
    });

    it('clearAll 清空全部', () => {
      useDevConsoleStore.getState().addNetworkRecord(makeNetworkRecord('1'));
      useDevConsoleStore.getState().addErrorRecord(makeErrorRecord('1'));
      trackPerf('x', 1);
      useDevConsoleStore.getState().clearAll();
      expect(useDevConsoleStore.getState().networkRecords).toHaveLength(0);
      expect(useDevConsoleStore.getState().errorRecords).toHaveLength(0);
      expect(useDevConsoleStore.getState().perfRecords).toHaveLength(0);
    });
  });

  describe('暂停与过滤', () => {
    it('togglePause 切换暂停状态', () => {
      expect(useDevConsoleStore.getState().paused).toBe(false);
      useDevConsoleStore.getState().togglePause();
      expect(useDevConsoleStore.getState().paused).toBe(true);
      useDevConsoleStore.getState().togglePause();
      expect(useDevConsoleStore.getState().paused).toBe(false);
    });

    it('setNetworkFilter 设置过滤关键字', () => {
      useDevConsoleStore.getState().setNetworkFilter('api');
      expect(useDevConsoleStore.getState().networkFilter).toBe('api');
    });
  });

  describe('install', () => {
    it('install 返回卸载函数', () => {
      const uninstall = useDevConsoleStore.getState().install();
      expect(typeof uninstall).toBe('function');
      // 不应抛错
      expect(() => uninstall()).not.toThrow();
    });
  });
});
