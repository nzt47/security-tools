/**
 * App 外壳插槽定义（任务 T2.2）
 *
 * 插槽 id 是稳定契约（见 PLAN-2 §5）：
 * - topbar  → 顶部状态区（StatusIndicator）
 * - sidebar → 左侧栏（Mascot / 面板入口 / 会话列表）
 * - main    → 主聊天区（ChatWindow + ChatInput）
 * - panels  → 浮层面板区（T2.3 接入 SkillManagement / Knowledge / DevConsole）
 *
 * mountAppSlots() 把外壳真实组件挂进对应插槽，在 App.tsx 模块顶层调用一次
 * （渲染前执行；mountToSlot 按 id 覆盖，重复调用幂等，HMR 重载安全）。
 */
import { mountToSlot } from './slotRegistry';
import {
  StatusEntry,
  SkillBtnEntry,
  KnowledgeBtnEntry,
  MascotEntry,
  SessionsEntry,
  ChatEntry,
} from './appSlots';

/** 外壳插槽 id 常量 */
export const SLOT_IDS = {
  topbar: 'topbar',
  sidebar: 'sidebar',
  main: 'main',
  panels: 'panels',
} as const;

export type SlotId = (typeof SLOT_IDS)[keyof typeof SLOT_IDS];

/**
 * 挂载 App 外壳默认组件（幂等）。
 * profile.json 的 order/hidden 会覆盖这里的默认顺序/显隐（见 slotRegistry.getSlotEntries）。
 */
export function mountAppSlots(): void {
  // topbar：系统状态
  mountToSlot(SLOT_IDS.topbar, { id: 'status', component: StatusEntry, order: 10 });

  // sidebar：面板入口 → Mascot → 会话列表
  mountToSlot(SLOT_IDS.sidebar, { id: 'skill', component: SkillBtnEntry, order: 5 });
  mountToSlot(SLOT_IDS.sidebar, { id: 'knowledge', component: KnowledgeBtnEntry, order: 6 });
  mountToSlot(SLOT_IDS.sidebar, { id: 'mascot', component: MascotEntry, order: 10 });
  mountToSlot(SLOT_IDS.sidebar, { id: 'sessions', component: SessionsEntry, order: 20 });

  // main：聊天窗口（含输入框）
  mountToSlot(SLOT_IDS.main, { id: 'chat', component: ChatEntry, order: 10 });
}
