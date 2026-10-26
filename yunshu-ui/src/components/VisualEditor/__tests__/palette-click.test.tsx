/**
 * ComponentPalette：点击 / 键盘也能添加节点。
 *
 * 2026-10-26 实测反馈：「可视化编辑点了都没反应」—— 面板项此前只有 draggable，
 * 单击不会做任何事。这里锁住"点击 = 添加"这条替代路径，避免回归成只能拖拽。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { ComponentPalette } from '../components/ComponentPalette';

afterEach(() => cleanup());

describe('ComponentPalette 点击添加', () => {
  it('点击组件触发 onPick（与拖拽等价）', () => {
    const onPick = vi.fn();
    render(<ComponentPalette onPick={onPick} />);
    fireEvent.click(screen.getByTestId('palette-item-skill'));
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0]).toMatchObject({ type: 'skill', label: '技能节点' });
  });

  it('键盘 Enter / 空格 也能添加（可键盘操作）', () => {
    const onPick = vi.fn();
    render(<ComponentPalette onPick={onPick} />);
    const item = screen.getByTestId('palette-item-loop');
    fireEvent.keyDown(item, { key: 'Enter' });
    fireEvent.keyDown(item, { key: ' ' });
    expect(onPick).toHaveBeenCalledTimes(2);
  });

  it('面板标题说明"点击添加，或拖拽到画布"', () => {
    render(<ComponentPalette />);
    expect(screen.getByText('点击添加，或拖拽到画布指定位置')).toBeTruthy();
  });
});
