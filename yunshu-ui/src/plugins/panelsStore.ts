/**
 * panelsStore —— 面板开关状态（任务 T2.3）
 *
 * 面板显隐状态集中管理：open[id] 表示面板条目是否打开。
 *
 * 初始化语义：
 * - 默认按 profile hidden 初始化（hidden:true → 初始关闭，按钮仍显示）；
 * - initOpen 以条目签名（id+hidden 序列）去重：签名未变（profile 未变）时
 *   跳过，保留用户手动开关状态；签名变化（profile 加载/重载）时按新配置重建。
 *
 * 生命周期：面板关闭时组件卸载（与改造前「关闭即卸载」一致），
 * DevConsole 的全局拦截器在挂载时启用、卸载时恢复。
 */
import { create } from 'zustand';

/** initOpen 的条目输入（只需 id 与 hidden，用于重建 open 与计算签名） */
export interface PanelOpenInput {
  id: string;
  hidden?: boolean;
}

interface PanelsState {
  /** 各面板是否打开（key = 面板条目 id） */
  open: Record<string, boolean>;
  /** 最近一次初始化时的条目签名；签名变化时按新 entries 重建 open */
  initKey: string | null;
  /** 按条目初始化 open（幂等：签名相同则跳过，保留用户手动状态） */
  initOpen: (entries: PanelOpenInput[], key: string) => void;
  /** 切换面板显隐 */
  toggle: (id: string) => void;
  /** 显式打开/关闭 */
  setOpen: (id: string, open: boolean) => void;
  /** 关闭单个面板 */
  close: (id: string) => void;
}

export const usePanelsStore = create<PanelsState>((set, get) => ({
  open: {},
  initKey: null,

  initOpen: (entries, key) => {
    if (get().initKey === key) return;
    const open: Record<string, boolean> = {};
    for (const e of entries) open[e.id] = !e.hidden;
    set({ open, initKey: key });
  },

  toggle: (id) => set((s) => ({ open: { ...s.open, [id]: !s.open[id] } })),
  setOpen: (id, v) => set((s) => ({ open: { ...s.open, [id]: v } })),
  close: (id) => set((s) => ({ open: { ...s.open, [id]: false } })),
}));
