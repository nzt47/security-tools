/**
 * useObservableState Hook 单元测试
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useObservableState } from './useObservableState';
import { useStateInspectorStore } from '../store';

describe('useObservableState', () => {
  beforeEach(() => {
    useStateInspectorStore.setState({
      snapshots: new Map(),
      timeline: [],
    });
  });

  it('初始值正确返回', () => {
    const { result } = renderHook(() => useObservableState('k1', 'init'));
    expect(result.current[0]).toBe('init');
  });

  it('初始挂载时注册快照到 store', () => {
    renderHook(() => useObservableState('k1', 'init'));
    const snap = useStateInspectorStore.getState().snapshots.get('k1');
    expect(snap).toBeDefined();
    expect(snap!.value).toBe('init');
  });

  it('setValue 更新本地状态', () => {
    const { result } = renderHook(() => useObservableState('k1', 'init'));
    act(() => {
      result.current[1]('updated');
    });
    expect(result.current[0]).toBe('updated');
  });

  it('setValue 同步上报快照到 store', () => {
    const { result } = renderHook(() => useObservableState('k1', 'init'));
    act(() => {
      result.current[1]('updated');
    });
    const snap = useStateInspectorStore.getState().snapshots.get('k1');
    expect(snap!.value).toBe('updated');
  });

  it('函数式更新', () => {
    const { result } = renderHook(() => useObservableState('counter', 0));
    act(() => {
      result.current[1]((prev) => prev + 1);
    });
    expect(result.current[0]).toBe(1);
    act(() => {
      result.current[1]((prev) => prev + 10);
    });
    expect(result.current[0]).toBe(11);
  });

  it('值变化时记录时间线 diff', () => {
    const { result } = renderHook(() => useObservableState('k1', 'v1'));
    act(() => {
      result.current[1]('v2');
    });
    const timeline = useStateInspectorStore.getState().timeline;
    expect(timeline).toHaveLength(1);
    expect(timeline[0].diffs[0].kind).toBe('updated');
  });

  it('options 透传（source / traceId / expiresAt）', () => {
    const { result } = renderHook(() =>
      useObservableState('k1', 'init', {
        source: 'localStorage',
        traceId: 'trace-1',
        expiresAt: 99999,
      })
    );
    const snap = useStateInspectorStore.getState().snapshots.get('k1');
    expect(snap!.source).toBe('localStorage');
    expect(snap!.traceId).toBe('trace-1');
    expect(snap!.expiresAt).toBe(99999);
  });

  it('setValue 上报失败不影响业务状态更新', () => {
    const { result } = renderHook(() => useObservableState('k1', 'init'));
    // 强制 store upsertSnapshot 抛错
    const spy = vi.spyOn(useStateInspectorStore.getState(), 'upsertSnapshot');
    spy.mockImplementation(() => {
      throw new Error('store error');
    });
    // 业务状态仍应更新
    act(() => {
      result.current[1]('updated');
    });
    expect(result.current[0]).toBe('updated');
    spy.mockRestore();
  });
});
