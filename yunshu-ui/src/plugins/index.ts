/**
 * 插槽体系核心汇总导出（任务 T2.1 / T2.2 / T2.3）
 *
 * 用法：
 *   import { SlotProvider, SlotHost, mountToSlot } from '@/plugins';
 *   import { SLOT_IDS, mountAppSlots } from '@/plugins/slots';
 *   import { mountPanels } from '@/plugins/panels';
 */
export {
  registerSlot,
  mountToSlot,
  unmountFromSlot,
  getSlotEntries,
  getAllSlotEntries,
  getManifestEntries,
  loadProfile,
  getProfile,
} from './slotRegistry';
export type { SlotEntry, SlotProfile } from './slotRegistry';
export { SlotHost } from './SlotHost';
export type { SlotHostProps } from './SlotHost';
export { SlotProvider } from './SlotProvider';
export { default as defaultProfile } from './profile.json';
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
