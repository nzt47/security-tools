/**
 * 插槽注册表核心（任务 T2.1）
 *
 * 三个核心原语：
 * 1. registerSlot(slotId)          —— 声明一个插槽（布局中的占位区域）
 * 2. mountToSlot / unmountFromSlot —— 把组件挂进/摘出插槽
 * 3. getSlotEntries(slotId)        —— 读取插槽内按序排列的组件
 *
 * Profile（配置驱动）：loadProfile() 加载 profile 描述的「每个插槽里挂哪些组件、
 * 顺序、显隐」；无 profile 条目时回退组件自身默认值（order 默认 100、不隐藏）。
 *
 * 参考实现：docs/yunshu-pluginization/PLAN-2-frontend-slots.md §2
 */
import React from 'react';

/** 挂入插槽的组件条目 */
export interface SlotEntry {
  /** 组件唯一 id，如 'mascot' */
  id: string;
  /** 要渲染的组件 */
  component: React.ComponentType;
  /** 排序，小在前，默认 100 */
  order?: number;
  /** profile 可置为 true 隐藏 */
  hidden?: boolean;
}

/** 插槽配置：slotId -> 该插槽内各组件条目的配置（不含 component） */
export interface SlotProfile {
  [slotId: string]: Array<{ id: string; order?: number; hidden?: boolean }>;
}

const slots = new Map<string, Map<string, SlotEntry>>();
let profile: SlotProfile = {};

/** 声明一个插槽；重复声明幂等，不会清空已挂载的条目 */
export function registerSlot(slotId: string): void {
  if (!slots.has(slotId)) slots.set(slotId, new Map());
}

/** 把组件条目挂入插槽；插槽不存在时自动创建 */
export function mountToSlot(slotId: string, entry: SlotEntry): void {
  registerSlot(slotId);
  slots.get(slotId)!.set(entry.id, entry);
}

/** 从插槽摘除组件条目；插槽或 id 不存在时静默忽略 */
export function unmountFromSlot(slotId: string, id: string): void {
  slots.get(slotId)?.delete(id);
}

/**
 * 读取插槽内按序排列的组件条目：
 * - 应用 profile 的 order/hidden，profile 缺失的字段回退组件自身默认值；
 * - 过滤 hidden 条目（条目本身仍保留在注册表，保持「可配置」）；
 * - 按 order 升序排序。
 */
export function getSlotEntries(slotId: string): SlotEntry[] {
  const entries = [...(slots.get(slotId)?.values() ?? [])];
  const cfg = profile[slotId] ?? [];
  return entries
    .map((e) => {
      const c = cfg.find((c) => c.id === e.id);
      return {
        ...e,
        order: c?.order ?? e.order ?? 100,
        hidden: c?.hidden ?? e.hidden ?? false,
      };
    })
    .filter((e) => !e.hidden)
    .sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
}

/** 加载 profile（整体替换当前配置） */
export function loadProfile(p: SlotProfile): void {
  profile = p;
}

/** 读取当前 profile */
export function getProfile(): SlotProfile {
  return profile;
}
