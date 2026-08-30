/**
 * pluginDiscovery —— 插件运行时发现（任务 T4.2，协议见 PLAN-4 §2）
 *
 * 职责：
 * 1. fetchPlugins()  —— GET /api/plugins 拉取 manifest，归一化为 PluginInfo
 *    （wire 形状与前端契约解耦：submit_url/client_slot（后端 snake_case）→
 *    submitUrl/clientSlot（前端 camelCase，兼容两种拼写）；
 *    schema 空 dict / None → null，routes 缺省 []）；
 * 2. reloadPlugins() —— POST /api/plugins/reload（后端扫描 plugins/ 目录重建
 *    注册表）成功后重新 GET 拉取最新清单；失败抛错（保留旧清单由调用方决定）；
 * 3. loadClientUi() —— 进阶：manifest 声明 clientSlot 的插件，用动态 import()
 *    加载其客户端模块并挂入插槽（约定：模块导出 register(registry)，或默认
 *    导出组件直接 mountToSlot）。动态加载失败仅抛错，不影响其他功能。
 *
 * 动态 import 约束：模块路径受 Vite public/（dev 下 /plugins/…）或 Flask
 * 静态目录（生产 /static/plugins/…）约束；importClientModule 对 /plugins/ 前缀
 * 做一次 /static 前缀回退，使 dev 与生产托管（build:flask 复制 public/plugins）
 * 都能工作。
 */
import React from 'react';
import { request } from '../lib/apiClient';
import * as slotRegistry from './slotRegistry';
import { usePanelsStore } from './panelsStore';

// ═══════════════════════════════════════════════════════════════
//  类型契约（任务 T4.2 设计契约：PluginInfo）
// ═══════════════════════════════════════════════════════════════

export interface ClientSlotInfo {
  slotId: string;
  module: string;
}

export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  schema: Record<string, any> | null;
  routes: string[];
  submitUrl?: string;
  clientSlot?: ClientSlotInfo | null;
}

/** 动态挂入插槽条目的 order（排在常规面板之后） */
export const DYNAMIC_ENTRY_ORDER = 999;

/** 动态挂载条目的稳定 id（按插件名，防重复挂载覆盖） */
export function dynamicEntryId(info: PluginInfo): string {
  return `dynamic:${info.name}`;
}

// ═══════════════════════════════════════════════════════════════
//  拉取 / 刷新
// ═══════════════════════════════════════════════════════════════

/** 把单个 manifest 条目归一化为 PluginInfo；非法条目返回 null（跳过） */
export function normalizePlugin(raw: unknown): PluginInfo | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const r = raw as Record<string, any>;
  if (typeof r.name !== 'string' || r.name.length === 0) return null;

  const schema =
    r.schema && typeof r.schema === 'object' && Object.keys(r.schema).length > 0 ? r.schema : null;
  const submitUrl = r.submit_url ?? r.submitUrl ?? '';
  const cs = r.client_slot ?? r.clientSlot ?? null;
  const clientSlot =
    cs && typeof cs === 'object' && typeof cs.slotId === 'string' && typeof cs.module === 'string'
      ? { slotId: cs.slotId, module: cs.module }
      : null;

  return {
    name: r.name,
    version: typeof r.version === 'string' ? r.version : '0.0.0',
    description: typeof r.description === 'string' ? r.description : '',
    schema,
    routes: Array.isArray(r.routes) ? r.routes.filter((x) => typeof x === 'string') : [],
    submitUrl: typeof submitUrl === 'string' ? submitUrl : '',
    clientSlot,
  };
}

/** GET /api/plugins：拉取并归一化插件清单 */
export async function fetchPlugins(signal?: AbortSignal): Promise<PluginInfo[]> {
  const manifest = await request<{ plugins?: unknown[] }>('/api/plugins', { signal });
  const list = Array.isArray(manifest?.plugins) ? manifest.plugins : [];
  return list.map(normalizePlugin).filter((p): p is PluginInfo => p !== null);
}

/** POST /api/plugins/reload 后再 GET：运行时发现新插件（无需重启进程） */
export async function reloadPlugins(signal?: AbortSignal): Promise<PluginInfo[]> {
  const res = await request<{ ok?: boolean; error?: string }>('/api/plugins/reload', {
    method: 'POST',
    signal,
  });
  if (res && res.ok === false) {
    throw new Error(res.error || '刷新插件清单失败');
  }
  // 契约：POST 成功后重新拉取（新插件立即可见）
  return fetchPlugins(signal);
}

// ═══════════════════════════════════════════════════════════════
//  客户端模块动态装载（进阶，T4.2 §3）
// ═══════════════════════════════════════════════════════════════

/**
 * 传给客户端模块 register(registry) 的注册表面。
 * 附带 React / createElement / panelsStore 助手，使 public/ 下的原生
 * ES 模块无需自行 import React 即可创建组件并挂入插槽。
 */
export interface SlotRegistryFacade {
  React: typeof React;
  createElement: typeof React.createElement;
  registerSlot: typeof slotRegistry.registerSlot;
  mountToSlot: typeof slotRegistry.mountToSlot;
  unmountFromSlot: typeof slotRegistry.unmountFromSlot;
  getSlotEntries: typeof slotRegistry.getSlotEntries;
  /** 运行时把条目追加进当前 profile（清单型消费方可见动态条目） */
  extendProfile: typeof slotRegistry.extendProfile;
  openPanel: (id: string) => void;
  closePanel: (id: string) => void;
}

/** 模块级单例注册表面（同一进程内所有动态插件共用） */
export const slotFacade: SlotRegistryFacade = {
  React,
  createElement: React.createElement,
  registerSlot: slotRegistry.registerSlot,
  mountToSlot: slotRegistry.mountToSlot,
  unmountFromSlot: slotRegistry.unmountFromSlot,
  getSlotEntries: slotRegistry.getSlotEntries,
  extendProfile: slotRegistry.extendProfile,
  openPanel: (id) => usePanelsStore.getState().setOpen(id, true),
  closePanel: (id) => usePanelsStore.getState().close(id),
};

/**
 * 应用已动态加载的客户端模块（约定，T4.2）：
 * - 导出 register(registry) → 交给模块自己挂载（可读 slotFacade 全量能力）；
 * - 仅默认导出（React 组件）→ 直接 mountToSlot + extendProfile + openPanel；
 * - 两者皆无 → 抛错（由调用方 Toast 提示，不影响其他功能）。
 */
export function applyClientModule(mod: unknown, info: PluginInfo): void {
  const cs = info.clientSlot;
  if (!cs) return;
  const m = mod as Record<string, unknown>;
  if (typeof m.register === 'function') {
    (m.register as (registry: SlotRegistryFacade) => unknown)(slotFacade);
    return;
  }
  const def = m.default;
  if (typeof def === 'function') {
    const id = dynamicEntryId(info);
    slotRegistry.mountToSlot(cs.slotId, {
      id,
      title: info.name,
      icon: '🧩',
      component: def as React.ComponentType,
      order: DYNAMIC_ENTRY_ORDER,
    });
    slotRegistry.extendProfile(cs.slotId, { id, order: DYNAMIC_ENTRY_ORDER, hidden: false });
    slotFacade.openPanel(id);
    return;
  }
  throw new Error(`插件「${info.name}」的客户端模块未导出 register() 或默认组件`);
}

/**
 * 动态 import 客户端模块。
 * dev 下 Vite 从 public/ 根提供 /plugins/…；生产 Flask 静态托管在
 * /static/plugins/…（build:flask 复制 dist/plugins）——对 /plugins/ 前缀
 * 回退一次 /static 前缀；两条路径都失败时抛原始错误。
 */
async function importClientModule(modulePath: string): Promise<unknown> {
  try {
    return await import(/* @vite-ignore */ modulePath);
  } catch (firstErr) {
    if (modulePath.startsWith('/plugins/')) {
      try {
        return await import(/* @vite-ignore */ `/static${modulePath}`);
      } catch {
        /* 生产路径也失败 → 抛原始（dev）错误 */
      }
    }
    throw firstErr;
  }
}

/** 动态装载插件客户端 UI：import 模块 → register/mount 挂入插槽 */
export async function loadClientUi(info: PluginInfo): Promise<void> {
  const cs = info.clientSlot;
  if (!cs || !cs.module) {
    throw new Error(`插件「${info.name}」未声明 clientSlot.module`);
  }
  const mod = await importClientModule(cs.module);
  applyClientModule(mod, info);
}
