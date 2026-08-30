/**
 * slotRegistry / SlotHost / SlotProvider 单元测试（任务 T2.1）
 *
 * 覆盖：registerSlot 幂等、mountToSlot 自动建插槽、getSlotEntries 排序与 hidden 过滤、
 * loadProfile 的 order/hidden 生效与默认值回退、unmountFromSlot 摘除、
 * SlotHost 渲染（按序 + profile 隐藏）、SlotProvider 注入默认 profile。
 *
 * 说明：注册表为模块级单例，测试间通过 loadProfile({}) 重置 profile，
 * 并使用互不相同的插槽 id，避免条目互相污染。
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import {
  registerSlot,
  mountToSlot,
  unmountFromSlot,
  getSlotEntries,
  loadProfile,
  getProfile,
} from './slotRegistry';
import { SlotHost } from './SlotHost';
import { SlotProvider } from './SlotProvider';
import defaultProfile from './profile.json';

// ── 组件挂载示例（任务要求：仅用于测试验证，不挂进 App） ──
// mountToSlot('sidebar', { id: 'demo', component: () => <div>demo</div>, order: 1 });

describe('slotRegistry 核心', () => {
  beforeEach(() => {
    // 重置 profile，避免测试间互相影响
    loadProfile({});
  });

  afterEach(() => {
    cleanup();
  });

  it('registerSlot 幂等：重复声明不报错、不清空已挂载条目', () => {
    mountToSlot('idem-slot', { id: 'a', component: () => <div>a</div> });
    expect(() => registerSlot('idem-slot')).not.toThrow();
    expect(() => registerSlot('idem-slot')).not.toThrow();
    expect(getSlotEntries('idem-slot').map((e) => e.id)).toEqual(['a']);
  });

  it('mountToSlot 自动建插槽：未先 registerSlot 也能挂载并读取', () => {
    mountToSlot('auto-slot', { id: 'demo', component: () => <div>demo</div>, order: 1 });
    const entries = getSlotEntries('auto-slot');
    expect(entries).toHaveLength(1);
    expect(entries[0].id).toBe('demo');
    expect(entries[0].order).toBe(1);
  });

  it('getSlotEntries 按 order 升序排序（缺省 order 视为 100）', () => {
    mountToSlot('order-slot', { id: 'first', component: () => <div>first</div>, order: 20 });
    mountToSlot('order-slot', { id: 'second', component: () => <div>second</div>, order: 10 });
    mountToSlot('order-slot', { id: 'third', component: () => <div>third</div> });
    expect(getSlotEntries('order-slot').map((e) => e.id)).toEqual(['second', 'first', 'third']);
  });

  it('getSlotEntries 过滤 hidden 条目（profile 与组件默认 hidden 均可）', () => {
    mountToSlot('hidden-slot', { id: 'visible', component: () => <div>visible</div> });
    mountToSlot('hidden-slot', { id: 'byProfile', component: () => <div>p</div> });
    mountToSlot('hidden-slot', { id: 'byDefault', component: () => <div>d</div>, hidden: true });
    loadProfile({ 'hidden-slot': [{ id: 'byProfile', hidden: true }] });
    expect(getSlotEntries('hidden-slot').map((e) => e.id)).toEqual(['visible']);
  });

  it('loadProfile 后 order/hidden 生效，覆盖组件默认值', () => {
    mountToSlot('profile-slot', { id: 'x', component: () => <div>x</div>, order: 5 });
    mountToSlot('profile-slot', { id: 'y', component: () => <div>y</div>, order: 5 });
    loadProfile({
      'profile-slot': [
        { id: 'x', order: 10 },
        { id: 'y', order: 1, hidden: true },
      ],
    });
    const entries = getSlotEntries('profile-slot');
    expect(entries.map((e) => e.id)).toEqual(['x']);
    expect(entries[0].order).toBe(10);
  });

  it('无 profile 条目时使用组件默认值（order 默认 100、hidden 默认 false）', () => {
    mountToSlot('default-slot', { id: 'a', component: () => <div>a</div> });
    mountToSlot('default-slot', { id: 'b', component: () => <div>b</div>, order: 7 });
    loadProfile({}); // 空 profile
    const entries = getSlotEntries('default-slot');
    expect(entries.find((e) => e.id === 'a')?.order).toBe(100);
    expect(entries.find((e) => e.id === 'b')?.order).toBe(7);
    expect(entries.every((e) => e.hidden === false)).toBe(true);
    // 按 order 升序：b(order=7) 在 a(order=100) 之前
    expect(entries.map((e) => e.id)).toEqual(['b', 'a']);
  });

  it('unmountFromSlot 摘除条目；不存在的插槽/id 静默', () => {
    mountToSlot('unmount-slot', { id: 'a', component: () => <div>a</div> });
    mountToSlot('unmount-slot', { id: 'b', component: () => <div>b</div> });
    unmountFromSlot('unmount-slot', 'a');
    expect(getSlotEntries('unmount-slot').map((e) => e.id)).toEqual(['b']);
    expect(() => unmountFromSlot('nope-slot', 'a')).not.toThrow();
    expect(() => unmountFromSlot('unmount-slot', 'nope')).not.toThrow();
  });
});

describe('SlotHost 渲染', () => {
  beforeEach(() => {
    loadProfile({});
  });

  afterEach(() => {
    cleanup();
  });

  it('挂载示例：mountToSlot("sidebar", demo) 可被 SlotHost 渲染', () => {
    mountToSlot('sidebar', { id: 'demo', component: () => <div>demo</div>, order: 1 });
    render(<SlotHost slotId="sidebar" />);
    expect(screen.getByText('demo')).toBeTruthy();
  });

  it('挂两个组件按序渲染，外层带 data-slot 与 className', () => {
    mountToSlot('host-order', { id: 'first', component: () => <div>first</div>, order: 20 });
    mountToSlot('host-order', { id: 'second', component: () => <div>second</div>, order: 10 });
    const { container } = render(<SlotHost slotId="host-order" className="host-cls" />);
    const host = container.querySelector('[data-slot="host-order"]');
    expect(host).not.toBeNull();
    expect(host!.className).toContain('host-cls');
    const rendered = Array.from(host!.children).map((c) => c.textContent);
    expect(rendered).toEqual(['second', 'first']);
  });

  it('profile 隐藏后不渲染该组件（条目仍保留在注册表）', () => {
    mountToSlot('host-hidden', { id: 'show', component: () => <div>show</div> });
    mountToSlot('host-hidden', { id: 'hide', component: () => <div>hide</div> });
    loadProfile({ 'host-hidden': [{ id: 'hide', hidden: true }] });
    const { container } = render(<SlotHost slotId="host-hidden" />);
    expect(screen.getByText('show')).toBeTruthy();
    expect(screen.queryByText('hide')).toBeNull();
    expect(container.querySelectorAll('[data-slot="host-hidden"] > *')).toHaveLength(1);
    // 摘除后条目确实还在注册表里（可配置性保留）
    unmountFromSlot('host-hidden', 'hide');
    expect(getSlotEntries('host-hidden').map((e) => e.id)).toEqual(['show']);
  });

  it('空插槽渲染空容器', () => {
    const { container } = render(<SlotHost slotId="empty-slot" />);
    const host = container.querySelector('[data-slot="empty-slot"]');
    expect(host).not.toBeNull();
    expect(host!.children).toHaveLength(0);
  });
});

describe('SlotProvider 注入', () => {
  afterEach(() => {
    cleanup();
  });

  it('挂载时加载默认 profile（展开 slots），children 透传', () => {
    render(
      <SlotProvider>
        <div data-testid="child">child</div>
      </SlotProvider>,
    );
    expect(screen.getByTestId('child')).toBeTruthy();
    const p = getProfile();
    expect(Object.keys(p)).toEqual(['topbar', 'sidebar', 'main', 'panels']);
    expect(p.topbar).toEqual([]);
    expect(p.sidebar).toEqual([]);
    expect(p.main).toEqual([]);
    expect(p.panels).toEqual([]);
    // 与 profile.json 内容一致
    expect(p).toEqual(defaultProfile.slots);
  });
});
