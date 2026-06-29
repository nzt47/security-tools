/**
 * StateInspector store 单元测试
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useStateInspectorStore, diffValues } from './store';

describe('StateInspector store', () => {
  beforeEach(() => {
    useStateInspectorStore.setState({
      snapshots: new Map(),
      timeline: [],
    });
  });

  describe('upsertSnapshot', () => {
    it('首次注册创建快照', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'value1', {
        source: 'memory',
      });
      const snap = useStateInspectorStore.getState().snapshots.get('key1');
      expect(snap).toBeDefined();
      expect(snap!.value).toBe('value1');
      expect(snap!.source).toBe('memory');
      expect(snap!.retryCount).toBe(0);
    });

    it('更新已有快照保留 expiresAt/retryCount', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1', {
        expiresAt: 99999,
        retryCount: 2,
      });
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v2');
      const snap = useStateInspectorStore.getState().snapshots.get('key1');
      expect(snap!.value).toBe('v2');
      expect(snap!.expiresAt).toBe(99999);
      expect(snap!.retryCount).toBe(2);
    });

    it('值变化时追加时间线条目', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1');
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v2');
      const timeline = useStateInspectorStore.getState().timeline;
      expect(timeline).toHaveLength(1);
      expect(timeline[0].key).toBe('key1');
      expect(timeline[0].diffs).toHaveLength(1);
      expect(timeline[0].diffs[0].kind).toBe('updated');
    });

    it('值未变化时不追加时间线', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1');
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1');
      expect(useStateInspectorStore.getState().timeline).toHaveLength(0);
    });

    it('traceId 透传到快照与时间线', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1', {
        traceId: 'trace-x',
      });
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v2', {
        traceId: 'trace-x',
      });
      const snap = useStateInspectorStore.getState().snapshots.get('key1');
      expect(snap!.traceId).toBe('trace-x');
      expect(useStateInspectorStore.getState().timeline[0].traceId).toBe('trace-x');
    });
  });

  describe('incrementRetry', () => {
    it('递增重试次数', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1');
      useStateInspectorStore.getState().incrementRetry('key1');
      useStateInspectorStore.getState().incrementRetry('key1');
      const snap = useStateInspectorStore.getState().snapshots.get('key1');
      expect(snap!.retryCount).toBe(2);
    });

    it('快照不存在时无副作用', () => {
      expect(() =>
        useStateInspectorStore.getState().incrementRetry('notexist')
      ).not.toThrow();
    });
  });

  describe('clearTimeline / clearAll', () => {
    it('clearTimeline 仅清空时间线', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1');
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v2');
      useStateInspectorStore.getState().clearTimeline();
      expect(useStateInspectorStore.getState().timeline).toHaveLength(0);
      expect(useStateInspectorStore.getState().snapshots.size).toBe(1);
    });

    it('clearAll 清空全部', () => {
      useStateInspectorStore.getState().upsertSnapshot('key1', 'v1');
      useStateInspectorStore.getState().clearAll();
      expect(useStateInspectorStore.getState().snapshots.size).toBe(0);
      expect(useStateInspectorStore.getState().timeline).toHaveLength(0);
    });
  });

  describe('diffValues', () => {
    it('相同值返回空 diff', () => {
      expect(diffValues(1, 1)).toHaveLength(0);
      expect(diffValues('a', 'a')).toHaveLength(0);
    });

    it('基本类型变化 → updated', () => {
      const diffs = diffValues(1, 2);
      expect(diffs).toHaveLength(1);
      expect(diffs[0].kind).toBe('updated');
    });

    it('对象新增字段 → added', () => {
      const diffs = diffValues({ a: 1 }, { a: 1, b: 2 });
      const added = diffs.find((d) => d.kind === 'added');
      expect(added).toBeDefined();
      expect(added!.path).toBe('b');
    });

    it('对象删除字段 → removed', () => {
      const diffs = diffValues({ a: 1, b: 2 }, { a: 1 });
      const removed = diffs.find((d) => d.kind === 'removed');
      expect(removed).toBeDefined();
      expect(removed!.path).toBe('b');
    });

    it('对象字段更新 → updated', () => {
      const diffs = diffValues({ a: 1 }, { a: 2 });
      const updated = diffs.find((d) => d.kind === 'updated');
      expect(updated).toBeDefined();
      expect(updated!.path).toBe('a');
    });

    it('数组变化整体标记 updated', () => {
      const diffs = diffValues([1, 2], [1, 2, 3]);
      expect(diffs).toHaveLength(1);
      expect(diffs[0].kind).toBe('updated');
    });

    it('类型不同 → updated', () => {
      const diffs = diffValues(1, '1');
      expect(diffs).toHaveLength(1);
      expect(diffs[0].kind).toBe('updated');
    });

    it('null 与对象 → updated', () => {
      const diffs = diffValues(null, { a: 1 });
      expect(diffs).toHaveLength(1);
      expect(diffs[0].kind).toBe('updated');
    });
  });

  describe('timeline LRU', () => {
    it('超过上限淘汰最旧', () => {
      // MAX_RECORDS 默认 200
      for (let i = 0; i < 202; i++) {
        useStateInspectorStore.getState().upsertSnapshot('key', i);
      }
      expect(useStateInspectorStore.getState().timeline).toHaveLength(200);
    });
  });
});
