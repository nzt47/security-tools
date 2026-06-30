/**
 * DevConsole 浮层容器组件测试
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import DevConsole from './DevConsole';
import { useDevConsoleStore } from './store';
import type { NetworkRecord, ErrorRecord, PerfRecord } from './types';

describe('DevConsole 组件', () => {
  beforeEach(() => {
    useDevConsoleStore.setState({
      networkRecords: [],
      errorRecords: [],
      perfRecords: [],
      paused: false,
      networkFilter: '',
    });
  });

  afterEach(() => cleanup());

  it('渲染 FAB 图标', () => {
    render(<DevConsole />);
    const fab = document.querySelector('.devconsole-fab');
    expect(fab).toBeTruthy();
  });

  it('错误数 > 0 时 FAB 显示 badge', () => {
    const err: ErrorRecord = {
      id: 'e1', type: 'onerror', message: 'err', stack: '', traceId: null, timestamp: Date.now(),
    };
    useDevConsoleStore.setState({ errorRecords: [err] });
    render(<DevConsole />);
    const badge = document.querySelector('.devconsole-fab-badge');
    expect(badge).toBeTruthy();
    expect(badge!.textContent).toBe('1');
  });

  it('defaultOpen=true 渲染面板与 Tab', () => {
    render(<DevConsole defaultOpen />);
    expect(screen.getByText('网络')).toBeTruthy();
    expect(screen.getByText('错误')).toBeTruthy();
    expect(screen.getByText('性能')).toBeTruthy();
  });

  it('点击 FAB（mousedown+mouseup 未移动）切换展开', () => {
    render(<DevConsole />);
    const fab = document.querySelector('.devconsole-fab')!;
    expect(screen.queryByText('网络')).toBeNull();
    // 模拟点击：mousedown → mouseup（未移动）
    fireEvent.mouseDown(fab, { button: 0, clientX: 100, clientY: 50 });
    fireEvent.mouseUp(window, { clientX: 100, clientY: 50 });
    expect(screen.getByText('网络')).toBeTruthy();
  });

  it('点击收起按钮关闭面板', () => {
    render(<DevConsole defaultOpen />);
    fireEvent.click(screen.getByTitle('收起'));
    expect(screen.queryByText('网络')).toBeNull();
  });

  it('切换 Tab 到错误', () => {
    const err: ErrorRecord = {
      id: 'e1', type: 'onerror', message: 'test-err', stack: '', traceId: null, timestamp: Date.now(),
    };
    useDevConsoleStore.setState({ errorRecords: [err] });
    render(<DevConsole defaultOpen />);
    fireEvent.click(screen.getByText('错误'));
    expect(screen.getByText('test-err')).toBeTruthy();
  });

  it('切换 Tab 到性能', () => {
    const perf: PerfRecord = {
      id: 'p1', name: 'render-test', duration: 5, timestamp: Date.now(),
    };
    useDevConsoleStore.setState({ perfRecords: [perf] });
    render(<DevConsole defaultOpen />);
    fireEvent.click(screen.getByText('性能'));
    expect(screen.getByText('render-test')).toBeTruthy();
  });

  it('暂停按钮切换采集状态', () => {
    render(<DevConsole defaultOpen />);
    const pauseBtn = screen.getByTitle('暂停采集');
    fireEvent.click(pauseBtn);
    expect(useDevConsoleStore.getState().paused).toBe(true);
    expect(screen.getByTitle('恢复采集')).toBeTruthy();
  });

  it('extraTabs 渲染额外 Tab 与内容', () => {
    render(
      <DevConsole
        defaultOpen
        extraTabs={[
          { key: 'extra', label: '额外', render: () => <div>extra-content</div> },
        ]}
      />
    );
    fireEvent.click(screen.getByText('额外'));
    expect(screen.getByText('extra-content')).toBeTruthy();
  });

  it('FAB 拖动（mousedown+mousemove 移动距离 > 3px）不切换展开', () => {
    render(<DevConsole />);
    const fab = document.querySelector('.devconsole-fab')!;
    fireEvent.mouseDown(fab, { button: 0, clientX: 100, clientY: 50 });
    fireEvent.mouseMove(window, { clientX: 150, clientY: 80 });
    fireEvent.mouseUp(window, { clientX: 150, clientY: 80 });
    // 拖动后不应展开
    expect(screen.queryByText('网络')).toBeNull();
  });

  it('网络记录数显示在 Tab', () => {
    const rec: NetworkRecord = {
      id: 'n1', url: '/api/x', method: 'GET', status: 200, duration: 10,
      traceId: null, timestamp: Date.now(), source: 'fetch',
    };
    useDevConsoleStore.setState({ networkRecords: [rec] });
    render(<DevConsole defaultOpen />);
    // 网络记录渲染
    expect(screen.getByText('/api/x')).toBeTruthy();
  });
});
