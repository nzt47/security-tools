/**
 * 插槽体系核心汇总导出（任务 T2.1 / T2.2）
 *
 * 用法：
 *   import { SlotProvider, SlotHost, mountToSlot } from '@/plugins';
 *   import { SLOT_IDS, mountAppSlots } from '@/plugins/slots';
 */
export {
  registerSlot,
  mountToSlot,
  unmountFromSlot,
  getSlotEntries,
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
  SkillBtnEntry,
  KnowledgeBtnEntry,
  MascotEntry,
  SessionsEntry,
  ChatEntry,
} from './appSlots';
