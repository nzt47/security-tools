/**
 * slotRegistry / SlotHost / SlotProvider 单元测试（任务 T2.1；T2.4 补回退与变体）
 *
 * 覆盖：registerSlot 幂等、mountToSlot 自动建插槽、getSlotEntries 排序与 hidden 过滤、
 * loadProfile 的 order/hidden 生效与默认值回退、unmountFromSlot 摘除、
 * SlotHost 渲染（按序 + profile 隐藏）、SlotProvider 注入默认 profile，
 * T2.4：profile 缺失/损坏/插槽缺失/条目缺失/声明未挂载的回退语义、reloadProfile 变体切换。
 *
 * 说明：注册表为模块级单例，测试间通过 loadProfile({}) 重置 profile，
 * 并使用互不相同的插槽 id，避免条目互相污染。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import {
  registerSlot,
  mountToSlot,
  unmountFromSlot,
  getSlotEntries,
  getAllSlotEntries,
  getManifestEntries,
  loadProfile,
  loadProfileFromRaw,
  reloadProfile,
  getProfile,
  DEFAULT_PROFILE,
} from './slotRegistry';
import { SlotHost } from './SlotHost';
import { SlotProvider } from './SlotProvider';

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

  it('SlotHost 通过 props 透传状态/回调到挂载组件（T2.2 新增能力）', () => {
    const SpyEntry = ({ label = 'none', onClick }: { label?: string; onClick?: () => void }) => (
      <button type="button" onClick={onClick}>
        {label}
      </button>
    );
    const onClick = vi.fn();
    mountToSlot('props-slot', { id: 'spy', component: SpyEntry });
    render(<SlotHost slotId="props-slot" props={{ label: 'hello', onClick }} />);
    const btn = screen.getByText('hello');
    expect(btn).toBeTruthy();
    btn.click();
    expect(onClick).toHaveBeenCalledTimes(1);
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

  it('挂载时从 profile.json 异步加载默认 profile（展开 slots），children 透传', async () => {
    render(
      <SlotProvider>
        <div data-testid="child">child</div>
      </SlotProvider>,
    );
    expect(screen.getByTestId('child')).toBeTruthy();
    // 等待异步 reloadProfile 完成（注册表初始为 DEFAULT_PROFILE，加载后为 profile.json 内容）
    await waitFor(() => {
      expect(getProfile()).toEqual(DEFAULT_PROFILE);
    });
    const p = getProfile();
    expect(Object.keys(p)).toEqual(['topbar', 'sidebar', 'main', 'panels']);
    // T2.2：profile.json 填充真实外壳条目
    expect(p.topbar).toEqual([{ id: 'status', order: 10 }]);
    // T2.3：侧栏面板入口合并为 PanelSwitcher（id 'panels'），原 skill/knowledge 按钮移除
    expect(p.sidebar?.map((s) => s.id)).toEqual(['panels', 'mascot', 'sessions']);
    expect(p.sidebar?.map((s) => s.order)).toEqual([5, 10, 20]);
    expect(p.main).toEqual([{ id: 'chat', order: 10 }]);
    // T2.3：panels 插槽填充面板（初始 hidden:true → 默认关闭，按钮仍显示）
    // T3.3：追加 plugin-center（插件中心）面板条目
    //（knowledge 面板已随知识库迁入工作台 memory/knowledge 移除）
    expect(p.panels).toEqual([
      { id: 'skills', order: 10, hidden: true },
      { id: 'devconsole', order: 30, hidden: true },
      { id: 'plugin-center', order: 40, hidden: true },
    ]);
  });
});

describe('getAllSlotEntries / getManifestEntries（T2.3 面板切换器数据源）', () => {
  beforeEach(() => {
    loadProfile({});
  });

  afterEach(() => {
    cleanup();
  });

  it('getAllSlotEntries 应用 profile 的 order/hidden 但不过滤 hidden，并透传 title/icon', () => {
    mountToSlot('panel-all', {
      id: 'a',
      component: () => <div>a</div>,
      order: 5,
      title: 'A 面板',
      icon: '⚙',
    });
    mountToSlot('panel-all', { id: 'b', component: () => <div>b</div>, order: 10 });
    loadProfile({ 'panel-all': [{ id: 'b', order: 1, hidden: true }] });
    const entries = getAllSlotEntries('panel-all');
    expect(entries.map((e) => e.id)).toEqual(['b', 'a']);
    expect(entries.find((e) => e.id === 'a')?.hidden).toBe(false);
    expect(entries.find((e) => e.id === 'a')?.title).toBe('A 面板');
    expect(entries.find((e) => e.id === 'a')?.icon).toBe('⚙');
    expect(entries.find((e) => e.id === 'b')?.hidden).toBe(true);
    // 同一 profile 下 getSlotEntries 仍过滤 hidden
    expect(getSlotEntries('panel-all').map((e) => e.id)).toEqual(['a']);
  });

  it('getManifestEntries 仅返回 profile 数组声明的条目（未声明的挂载条目不返回）', () => {
    mountToSlot('panel-manifest', { id: 'x', component: () => <div>x</div>, order: 5 });
    mountToSlot('panel-manifest', { id: 'y', component: () => <div>y</div>, order: 10 });
    mountToSlot('panel-manifest', { id: 'z', component: () => <div>z</div>, order: 3 });
    loadProfile({
      'panel-manifest': [
        { id: 'y', order: 1, hidden: true },
        { id: 'x', order: 2 },
      ],
    });
    const entries = getManifestEntries('panel-manifest');
    expect(entries.map((e) => e.id)).toEqual(['y', 'x']);
    expect(entries.find((e) => e.id === 'y')?.hidden).toBe(true);
    expect(entries.find((e) => e.id === 'x')?.hidden).toBe(false);
  });

  it('getManifestEntries 无 profile 配置时回退全部已挂载条目（组件默认 order/hidden）', () => {
    mountToSlot('panel-fallback', { id: 'm', component: () => <div>m</div>, order: 7 });
    loadProfile({});
    const entries = getManifestEntries('panel-fallback');
    expect(entries.map((e) => e.id)).toEqual(['m']);
    expect(entries[0].order).toBe(7);
    expect(entries[0].hidden).toBe(false);
  });
});

describe('T2.4 profile 回退与变体', () => {
  beforeEach(() => {
    // 重置 profile，避免测试间互相影响
    loadProfile({});
  });

  afterEach(() => {
    cleanup();
  });

  it('DEFAULT_PROFILE 与 profile.json 内容一致（回退兜底不漂移）', async () => {
    // 经真实加载路径验证：profile.json 存在且解析成功，且内容与代码内默认一致
    expect(await reloadProfile('profile.json')).toBe(true);
    expect(getProfile()).toEqual(DEFAULT_PROFILE);
  });

  it('profile 缺失：reloadProfile 加载不存在的变体 → false 并回退 DEFAULT_PROFILE', async () => {
    expect(await reloadProfile('profile.not-exist.json')).toBe(false);
    expect(getProfile()).toEqual(DEFAULT_PROFILE);
  });

  it('profile 损坏：JSON 语法错误 → 不抛错、回退 DEFAULT_PROFILE', () => {
    expect(() => loadProfileFromRaw('{ not valid json !!')).not.toThrow();
    expect(loadProfileFromRaw('{ not valid json !!')).toBe(false);
    expect(getProfile()).toEqual(DEFAULT_PROFILE);
  });

  it('profile 结构无效（slots 非对象）→ 回退 DEFAULT_PROFILE', () => {
    expect(loadProfileFromRaw('{"slots": 42}')).toBe(false);
    expect(getProfile()).toEqual(DEFAULT_PROFILE);
  });

  it('loadProfile 无效输入（null）→ 回退 DEFAULT_PROFILE', () => {
    loadProfile(null as unknown);
    expect(getProfile()).toEqual(DEFAULT_PROFILE);
  });

  it('插槽缺失：profile 未声明该插槽 → 渲染全部已挂载条目（组件默认 order/hidden）', () => {
    mountToSlot('miss-slot', { id: 'a', component: () => <div>a</div>, order: 7 });
    mountToSlot('miss-slot', { id: 'b', component: () => <div>b</div> });
    loadProfile({ 'other-slot': [{ id: 'x', order: 1 }] });
    const entries = getSlotEntries('miss-slot');
    expect(entries.map((e) => e.id)).toEqual(['a', 'b']); // a(order=7) 在 b(默认100) 前
    expect(entries[0].order).toBe(7);
    expect(entries[1].order).toBe(100);
    expect(entries.every((e) => e.hidden === false)).toBe(true);
  });

  it('条目缺失：profile 只声明部分条目 → 未声明条目用组件默认 order/hidden', () => {
    mountToSlot('part-slot', { id: 'a', component: () => <div>a</div>, order: 7, hidden: true });
    mountToSlot('part-slot', { id: 'b', component: () => <div>b</div>, order: 3 });
    loadProfile({ 'part-slot': [{ id: 'b', order: 1 }] });
    const all = getAllSlotEntries('part-slot');
    const a = all.find((e) => e.id === 'a')!;
    const b = all.find((e) => e.id === 'b')!;
    expect(a.order).toBe(7); // 组件默认 order 保留
    expect(a.hidden).toBe(true); // 组件默认 hidden 保留
    expect(b.order).toBe(1); // profile order 覆盖
    // 渲染视角：hidden 的 a 被过滤，b 按 profile order 排最前
    expect(getSlotEntries('part-slot').map((e) => e.id)).toEqual(['b']);
  });

  it('条目声明但未挂载：忽略、不渲染、不抛错', () => {
    mountToSlot('ghost-slot', { id: 'real', component: () => <div>real</div> });
    loadProfile({
      'ghost-slot': [
        { id: 'real', order: 2 },
        { id: 'ghost', order: 1 },
      ],
    });
    expect(() => getSlotEntries('ghost-slot')).not.toThrow();
    expect(getSlotEntries('ghost-slot').map((e) => e.id)).toEqual(['real']);
  });

  it('条目顺序完全由 profile 决定：profile order 覆盖组件默认 order', () => {
    mountToSlot('order-ovr', { id: 'x', component: () => <div>x</div>, order: 5 });
    mountToSlot('order-ovr', { id: 'y', component: () => <div>y</div>, order: 5 });
    loadProfile({
      'order-ovr': [
        { id: 'x', order: 20 },
        { id: 'y', order: 10 },
      ],
    });
    expect(getSlotEntries('order-ovr').map((e) => e.id)).toEqual(['y', 'x']);
  });

  it('支持 { slots: {...} } 容器形状（与平铺结构等价）', () => {
    mountToSlot('wrap-slot', { id: 'a', component: () => <div>a</div> });
    loadProfile({ slots: { 'wrap-slot': [{ id: 'a', order: 5, hidden: true }] } });
    const all = getAllSlotEntries('wrap-slot');
    expect(all[0].order).toBe(5);
    expect(all[0].hidden).toBe(true);
  });

  it('profile.alt.json 变体生效并可切回默认（sidebar 换序 + 面板调整）', async () => {
    expect(await reloadProfile('profile.alt.json')).toBe(true);
    // sidebar 顺序交换：mascot 置顶、panels 沉底
    expect(getProfile().sidebar?.map((s) => s.id)).toEqual(['mascot', 'sessions', 'panels']);
    expect(getProfile().sidebar?.map((s) => s.order)).toEqual([5, 10, 20]);
    // panels：skills 默认打开（hidden:false）、devconsole 不在清单
    // （knowledge 面板已随知识库迁入工作台移除）
    expect(getProfile().panels).toEqual([
      { id: 'skills', order: 10, hidden: false },
    ]);
    // 切回默认 profile 恢复
    expect(await reloadProfile()).toBe(true);
    expect(getProfile()).toEqual(DEFAULT_PROFILE);
  });
});
