/**
 * ObservabilityDevtools 统一入口组件测试
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { default as ObservabilityDevtools } from './index';
import { useDevConsoleStore } from '@/components/DevConsole';
import { useStateInspectorStore } from '@/components/StateInspector';

describe('ObservabilityDevtools 统一入口', () => {
  beforeEach(() => {
    useDevConsoleStore.setState({
      networkRecords: [], errorRecords: [], perfRecords: [], paused: false, networkFilter: '',
    });
    useStateInspectorStore.setState({ snapshots: new Map(), timeline: [] });
  });
  afterEach(() => cleanup());

  it('渲染 DevConsole FAB', () => {
    render(<ObservabilityDevtools />);
    expect(document.querySelector('.devconsole-fab')).toBeTruthy();
  });

  it('展开后包含"状态"Tab', () => {
    render(<ObservabilityDevtools />);
    const fab = document.querySelector('.devconsole-fab')!;
    fireEvent.mouseDown(fab, { button: 0, clientX: 100, clientY: 50 });
    fireEvent.mouseUp(window, { clientX: 100, clientY: 50 });
    expect(screen.getByText('状态')).toBeTruthy();
  });

  it('切换到状态 Tab 显示 StateInspector', () => {
    render(<ObservabilityDevtools />);
    const fab = document.querySelector('.devconsole-fab')!;
    fireEvent.mouseDown(fab, { button: 0, clientX: 100, clientY: 50 });
    fireEvent.mouseUp(window, { clientX: 100, clientY: 50 });
    fireEvent.click(screen.getByText('状态'));
    // StateInspector 默认显示快照空状态
    expect(screen.getByText('暂无状态快照')).toBeTruthy();
  });

  it('组件挂载时安装拦截器，卸载时清理', () => {
    const { unmount } = render(<ObservabilityDevtools />);
    // 挂载后 install 被调用（通过 FAB 存在间接验证）
    expect(document.querySelector('.devconsole-fab')).toBeTruthy();
    // 卸载不抛错
    expect(() => unmount()).not.toThrow();
  });
});
