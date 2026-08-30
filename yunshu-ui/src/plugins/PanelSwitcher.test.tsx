/**
 * PanelSwitcher / PanelHost 单元测试（任务 T2.3）
 *
 * 覆盖（对应验收项）：
 * - 渲染每个面板的开关按钮（title/icon）；
 * - 点击按钮切换显隐（zustand panelsStore.open）；
 * - profile hidden 生效：hidden:true 的面板初始关闭（不渲染内容），点击后打开；
 * - 多面板可同时打开（多开）；
 * - profile order 生效：按钮按 order 排序；
 * - profile.json 的 panels 数组移除某条目 → 切换器不再显示该按钮；
 * - 无 profile panels 配置时回退到全部已挂载条目。
 *
 * 说明：注册表/panelsStore 均为模块级单例，beforeEach 清空 panels 插槽并挂载
 * 假条目，避免与其他测试（App.test 挂载真实面板）互相污染。
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import {
  mountToSlot,
  unmountFromSlot,
  getAllSlotEntries,
  loadProfile,
} from './slotRegistry';
import { PanelSwitcher } from './PanelSwitcher';
import { PanelFrame } from './panels';
import { usePanelsStore } from './panelsStore';

const PANELS = 'panels';

/** 清空 panels 插槽内全部条目（含其他测试挂载的真实面板） */
function resetPanelsSlot() {
  for (const e of getAllSlotEntries(PANELS)) {
    unmountFromSlot(PANELS, e.id);
  }
}

/** 挂载三个假面板（纯文本组件，避免触发真实面板的网络请求） */
function mountFakePanels() {
  mountToSlot(PANELS, {
    id: 'skills',
    title: '技能管理',
    icon: '⚙',
    component: () => (
      <PanelFrame id="skills" title="技能管理" icon="⚙">
        <div>SKILLS_BODY</div>
      </PanelFrame>
    ),
    order: 10,
  });
  mountToSlot(PANELS, {
    id: 'knowledge',
    title: '知识库',
    icon: '📚',
    component: () => (
      <PanelFrame id="knowledge" title="知识库" icon="📚">
        <div>KNOWLEDGE_BODY</div>
      </PanelFrame>
    ),
    order: 20,
  });
  // DevConsole 为自定位浮层，无 PanelFrame（对应真实 ObservabilityDevtools）
  mountToSlot(PANELS, {
    id: 'devconsole',
    title: 'DevConsole',
    icon: '🐛',
    component: () => <div>DEV_BODY</div>,
    order: 30,
  });
}

const PROFILE_ALL_HIDDEN = {
  panels: [
    { id: 'skills', order: 10, hidden: true },
    { id: 'knowledge', order: 20, hidden: true },
    { id: 'devconsole', order: 30, hidden: true },
  ],
};

describe('PanelSwitcher', () => {
  beforeEach(() => {
    resetPanelsSlot();
    mountFakePanels();
    loadProfile(PROFILE_ALL_HIDDEN);
    usePanelsStore.setState({ open: {}, initKey: null });
  });

  afterEach(() => {
    cleanup();
    loadProfile({});
    resetPanelsSlot();
  });

  it('渲染每个面板的开关按钮（title/icon）', () => {
    render(<PanelSwitcher />);
    // 按钮 aria-label 为 title（精确匹配，与浮层关闭按钮「关闭X」不冲突）
    expect(screen.getByRole('button', { name: '技能管理' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '知识库' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'DevConsole' })).toBeTruthy();
    // 按钮包含 icon 与 title 文本
    expect(screen.getByRole('button', { name: '技能管理' }).textContent).toContain('⚙');
    expect(screen.getByRole('button', { name: '知识库' }).textContent).toContain('📚');
  });

  it('profile hidden 生效：hidden:true 的面板初始关闭，点击后打开、再点关闭', () => {
    render(<PanelSwitcher />);
    // 初始：面板未打开、内容不渲染
    expect(usePanelsStore.getState().open['skills']).toBe(false);
    expect(screen.queryByText('SKILLS_BODY')).toBeNull();
    // 点击打开
    fireEvent.click(screen.getByRole('button', { name: '技能管理' }));
    expect(usePanelsStore.getState().open['skills']).toBe(true);
    expect(screen.getByText('SKILLS_BODY')).toBeTruthy();
    // 再点关闭 → 组件卸载（与改造前「关闭即卸载」一致）
    fireEvent.click(screen.getByRole('button', { name: '技能管理' }));
    expect(usePanelsStore.getState().open['skills']).toBe(false);
    expect(screen.queryByText('SKILLS_BODY')).toBeNull();
  });

  it('多面板可同时打开（多开）', () => {
    render(<PanelSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: '技能管理' }));
    fireEvent.click(screen.getByRole('button', { name: '知识库' }));
    expect(screen.getByText('SKILLS_BODY')).toBeTruthy();
    expect(screen.getByText('KNOWLEDGE_BODY')).toBeTruthy();
    // 关闭其中一个，另一个仍打开
    fireEvent.click(screen.getByRole('button', { name: '技能管理' }));
    expect(screen.queryByText('SKILLS_BODY')).toBeNull();
    expect(screen.getByText('KNOWLEDGE_BODY')).toBeTruthy();
  });

  it('profile order 生效：按钮按 order 升序渲染', () => {
    loadProfile({
      panels: [
        { id: 'devconsole', order: 5, hidden: true },
        { id: 'skills', order: 10, hidden: true },
        { id: 'knowledge', order: 20, hidden: true },
      ],
    });
    render(<PanelSwitcher />);
    const buttons = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    const idx = (t: string) => buttons.findIndex((x) => x.includes(t));
    expect(idx('DevConsole')).toBeLessThan(idx('技能管理'));
    expect(idx('技能管理')).toBeLessThan(idx('知识库'));
  });

  it('修改 profile.json：panels 数组移除某条目 → 切换器不再显示该按钮', () => {
    loadProfile({
      panels: [
        { id: 'skills', order: 10, hidden: true },
        { id: 'knowledge', order: 20, hidden: true },
      ],
    });
    render(<PanelSwitcher />);
    expect(screen.getByRole('button', { name: '技能管理' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '知识库' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'DevConsole' })).toBeNull();
  });

  it('无 profile panels 配置时回退到全部已挂载条目', () => {
    loadProfile({});
    render(<PanelSwitcher />);
    expect(screen.getByRole('button', { name: '技能管理' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '知识库' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'DevConsole' })).toBeTruthy();
  });

  it('打开面板后可通过浮层关闭按钮关闭', () => {
    render(<PanelSwitcher />);
    fireEvent.click(screen.getByRole('button', { name: '知识库' }));
    expect(screen.getByText('KNOWLEDGE_BODY')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '关闭知识库' }));
    expect(usePanelsStore.getState().open['knowledge']).toBe(false);
    expect(screen.queryByText('KNOWLEDGE_BODY')).toBeNull();
  });
});
