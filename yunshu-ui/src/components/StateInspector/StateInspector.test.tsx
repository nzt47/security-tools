/**
 * StateInspector 面板组件测试
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import StateInspector from './StateInspector';
import { useStateInspectorStore } from './store';
import type { StateSnapshot, StateTimelineEntry } from './types';

describe('StateInspector 组件', () => {
  beforeEach(() => {
    useStateInspectorStore.setState({
      snapshots: new Map(),
      timeline: [],
    });
  });
  afterEach(() => cleanup());

  it('快照视图空状态', () => {
    render(<StateInspector />);
    expect(screen.getByText('暂无状态快照')).toBeTruthy();
  });

  it('渲染状态快照', () => {
    const snap: StateSnapshot = {
      key: 'chatInput', value: 'hello', updatedAt: Date.now(),
      expiresAt: 0, retryCount: 0, traceId: null, source: 'memory',
    };
    const map = new Map([['chatInput', snap]]);
    useStateInspectorStore.setState({ snapshots: map });
    render(<StateInspector />);
    expect(screen.getByText('chatInput')).toBeTruthy();
    expect(screen.getByText('hello')).toBeTruthy();
  });

  it('展示重试次数 badge', () => {
    const snap: StateSnapshot = {
      key: 'req', value: 'v', updatedAt: Date.now(),
      expiresAt: 0, retryCount: 3, traceId: null, source: 'memory',
    };
    useStateInspectorStore.setState({ snapshots: new Map([['req', snap]]) });
    render(<StateInspector />);
    expect(screen.getByText('重试 3 次')).toBeTruthy();
  });

  it('展示 source badge', () => {
    const snap: StateSnapshot = {
      key: 'k', value: 'v', updatedAt: Date.now(),
      expiresAt: 0, retryCount: 0, traceId: null, source: 'localStorage',
    };
    useStateInspectorStore.setState({ snapshots: new Map([['k', snap]]) });
    render(<StateInspector />);
    expect(screen.getByText('localStorage')).toBeTruthy();
  });

  it('展示过期倒计时', () => {
    const snap: StateSnapshot = {
      key: 'k', value: 'v', updatedAt: Date.now(),
      expiresAt: Date.now() + 5000, retryCount: 0, traceId: null, source: 'memory',
    };
    useStateInspectorStore.setState({ snapshots: new Map([['k', snap]]) });
    render(<StateInspector />);
    expect(screen.getByText(/剩余/)).toBeTruthy();
  });

  it('展示已过期状态', () => {
    const snap: StateSnapshot = {
      key: 'k', value: 'v', updatedAt: Date.now() - 10000,
      expiresAt: Date.now() - 1000, retryCount: 0, traceId: null, source: 'memory',
    };
    useStateInspectorStore.setState({ snapshots: new Map([['k', snap]]) });
    render(<StateInspector />);
    expect(screen.getByText('已过期（将触发重新获取）')).toBeTruthy();
  });

  it('切换到时间线视图', () => {
    const entry: StateTimelineEntry = {
      id: 't1', key: 'k', timestamp: Date.now(),
      diffs: [{ path: 'field', oldValue: 1, newValue: 2, kind: 'updated' }],
      traceId: null,
    };
    useStateInspectorStore.setState({ timeline: [entry] });
    render(<StateInspector />);
    fireEvent.click(screen.getByText(/时间线/));
    expect(screen.getByText(/~ 更新/)).toBeTruthy();
  });

  it('时间线空状态', () => {
    render(<StateInspector />);
    fireEvent.click(screen.getByText(/时间线/));
    expect(screen.getByText('暂无状态变更记录')).toBeTruthy();
  });

  it('清空全部', () => {
    const snap: StateSnapshot = {
      key: 'k', value: 'v', updatedAt: Date.now(),
      expiresAt: 0, retryCount: 0, traceId: null, source: 'memory',
    };
    useStateInspectorStore.setState({ snapshots: new Map([['k', snap]]) });
    render(<StateInspector />);
    fireEvent.click(screen.getByText('清空全部'));
    expect(useStateInspectorStore.getState().snapshots.size).toBe(0);
  });

  it('diff 显示 added/removed/updated', () => {
    const entries: StateTimelineEntry[] = [
      {
        id: 't1', key: 'k', timestamp: Date.now(),
        diffs: [
          { path: 'a', oldValue: undefined, newValue: 1, kind: 'added' },
          { path: 'b', oldValue: 2, newValue: undefined, kind: 'removed' },
          { path: 'c', oldValue: 3, newValue: 4, kind: 'updated' },
        ],
        traceId: null,
      },
    ];
    useStateInspectorStore.setState({ timeline: entries });
    render(<StateInspector />);
    fireEvent.click(screen.getByText(/时间线/));
    expect(screen.getByText(/\+ 新增/)).toBeTruthy();
    expect(screen.getByText(/- 删除/)).toBeTruthy();
    expect(screen.getByText(/~ 更新/)).toBeTruthy();
  });
});
