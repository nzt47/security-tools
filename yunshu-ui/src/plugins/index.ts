/**
 * 插槽体系核心汇总导出（任务 T2.1 / T2.2 / T2.3 / T2.4）
 *
 * 用法：
 *   import { SlotProvider, SlotHost, mountToSlot } from '@/plugins';
 *   import { SLOT_IDS, mountAppSlots } from '@/plugins/slots';
 *   import { mountPanels } from '@/plugins/panels';
 *   import { reloadProfile } from '@/plugins';   // 运行时切换 profile 变体
 */
export {
  registerSlot,
  mountToSlot,
  unmountFromSlot,
  getSlotEntries,
  getAllSlotEntries,
  getManifestEntries,
  loadProfile,
  loadProfileFromRaw,
  reloadProfile,
  normalizeProfile,
  getProfile,
  DEFAULT_PROFILE,
} from './slotRegistry';
export type { SlotEntry, SlotProfile, SlotProfileItem } from './slotRegistry';
export { SlotHost } from './SlotHost';
export type { SlotHostProps } from './SlotHost';
export { SlotProvider } from './SlotProvider';
// T2.4：defaultProfile 指向代码内 DEFAULT_PROFILE（不再静态依赖 profile.json——
// 文件缺失/损坏时不影响构建与启动，由 reloadProfile 回退兜底）
export { DEFAULT_PROFILE as defaultProfile } from './slotRegistry';
export { SLOT_IDS, mountAppSlots } from './slots';
export type { SlotId } from './slots';
export {
  StatusEntry,
  MascotEntry,
  SessionsEntry,
  ChatEntry,
} from './appSlots';
// 面板系统（T2.3）
export { usePanelsStore } from './panelsStore';
export type { PanelOpenInput } from './panelsStore';
export { PanelSwitcher } from './PanelSwitcher';
export { PanelHost, PANELS_SLOT_ID } from './PanelHost';
export type { PanelHostProps } from './PanelHost';
export { mountPanels, PanelFrame, PANEL_IDS } from './panels';
