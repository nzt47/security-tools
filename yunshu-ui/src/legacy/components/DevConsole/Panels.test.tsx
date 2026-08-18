/**
 * NetworkPanel / ErrorPanel / PerformancePanel 组件测试
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { useDevConsoleStore } from './store';
import NetworkPanel from './NetworkPanel';
import ErrorPanel from './ErrorPanel';
import PerformancePanel from './PerformancePanel';
import type { NetworkRecord, ErrorRecord, PerfRecord } from './types';

function netRecord(over: Partial<NetworkRecord> = {}): NetworkRecord {
  return {
    id: 'n1', url: '/api/test', method: 'GET', status: 200, duration: 10,
    traceId: 'traceabc', timestamp: Date.now(), source: 'fetch', ...over,
  };
}
function errRecord(over: Partial<ErrorRecord> = {}): ErrorRecord {
  return {
    id: 'e1', type: 'onerror', message: 'test error', stack: 'stack-line',
    traceId: 'traceabc', timestamp: Date.now(), source: 'err.js',
    lineno: 10, colno: 5, ...over,
  };
}
function perfRecord(over: Partial<PerfRecord> = {}): PerfRecord {
  return {
    id: 'p1', name: 'render-x', duration: 5, timestamp: Date.now(), ...over,
  };
}

describe('NetworkPanel', () => {
  beforeEach(() => {
    useDevConsoleStore.setState({
      networkRecords: [], errorRecords: [], perfRecords: [], paused: false, networkFilter: '',
    });
  });
  afterEach(() => cleanup());

  it('空状态展示提示', () => {
    render(<NetworkPanel />);
    expect(screen.getByText('暂无网络请求记录')).toBeTruthy();
  });

  it('渲染网络记录', () => {
    useDevConsoleStore.setState({ networkRecords: [netRecord()] });
    render(<NetworkPanel />);
    expect(screen.getByText('/api/test')).toBeTruthy();
    expect(screen.getByText('GET')).toBeTruthy();
  });

  it('点击行展开详情', () => {
    useDevConsoleStore.setState({ networkRecords: [netRecord({ requestBody: '{"k":"v"}' })] });
    render(<NetworkPanel />);
    fireEvent.click(screen.getByText('/api/test'));
    expect(screen.getByText('请求体：')).toBeTruthy();
  });

  it('过滤关键字', () => {
    useDevConsoleStore.setState({
      networkRecords: [
        netRecord({ id: 'a', url: '/api/users' }),
        netRecord({ id: 'b', url: '/api/posts' }),
      ],
    });
    render(<NetworkPanel />);
    fireEvent.change(screen.getByPlaceholderText('过滤 URL / 状态码 / trace_id'), {
      target: { value: 'users' },
    });
    expect(screen.getByText('/api/users')).toBeTruthy();
    expect(screen.queryByText('/api/posts')).toBeNull();
  });

  it('清空按钮', () => {
    useDevConsoleStore.setState({ networkRecords: [netRecord()] });
    render(<NetworkPanel />);
    fireEvent.click(screen.getByText('清空'));
    expect(useDevConsoleStore.getState().networkRecords).toHaveLength(0);
  });

  it('无 trace_id 时显示 -', () => {
    useDevConsoleStore.setState({ networkRecords: [netRecord({ traceId: null })] });
    render(<NetworkPanel />);
    expect(screen.getByText('-')).toBeTruthy();
  });

  it('错误状态显示 ERR', () => {
    useDevConsoleStore.setState({ networkRecords: [netRecord({ status: 0, error: 'fail' })] });
    render(<NetworkPanel />);
    expect(screen.getByText('ERR')).toBeTruthy();
  });
});

describe('ErrorPanel', () => {
  beforeEach(() => {
    useDevConsoleStore.setState({
      networkRecords: [], errorRecords: [], perfRecords: [], paused: false, networkFilter: '',
    });
  });
  afterEach(() => cleanup());

  it('空状态展示提示', () => {
    render(<ErrorPanel />);
    expect(screen.getByText('暂无错误记录')).toBeTruthy();
  });

  it('渲染错误记录', () => {
    useDevConsoleStore.setState({ errorRecords: [errRecord()] });
    render(<ErrorPanel />);
    expect(screen.getByText('test error')).toBeTruthy();
  });

  it('点击展开堆栈', () => {
    useDevConsoleStore.setState({ errorRecords: [errRecord()] });
    render(<ErrorPanel />);
    fireEvent.click(screen.getByText('test error'));
    expect(screen.getByText('stack-line')).toBeTruthy();
  });

  it('unhandledrejection 类型显示 Promise', () => {
    useDevConsoleStore.setState({
      errorRecords: [errRecord({ type: 'unhandledrejection' })],
    });
    render(<ErrorPanel />);
    expect(screen.getByText('Promise')).toBeTruthy();
  });

  it('清空按钮', () => {
    useDevConsoleStore.setState({ errorRecords: [errRecord()] });
    render(<ErrorPanel />);
    fireEvent.click(screen.getByText('清空'));
    expect(useDevConsoleStore.getState().errorRecords).toHaveLength(0);
  });
});

describe('PerformancePanel', () => {
  beforeEach(() => {
    useDevConsoleStore.setState({
      networkRecords: [], errorRecords: [], perfRecords: [], paused: false, networkFilter: '',
    });
  });
  afterEach(() => cleanup());

  it('空状态展示提示', () => {
    render(<PerformancePanel />);
    expect(screen.getByText('暂无性能记录')).toBeTruthy();
  });

  it('渲染性能记录与统计', () => {
    useDevConsoleStore.setState({
      perfRecords: [perfRecord(), perfRecord({ id: 'p2', duration: 50 })],
    });
    render(<PerformancePanel />);
    expect(screen.getAllByText('render-x').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/共 2 条/)).toBeTruthy();
  });

  it('清空按钮', () => {
    useDevConsoleStore.setState({ perfRecords: [perfRecord()] });
    render(<PerformancePanel />);
    fireEvent.click(screen.getByText('清空'));
    expect(useDevConsoleStore.getState().perfRecords).toHaveLength(0);
  });
});
