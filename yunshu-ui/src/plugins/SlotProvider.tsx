/**
 * SlotProvider —— 注入默认 profile（任务 T2.1，T2.3 起渲染期加载）
 *
 * 挂载时 loadProfile(默认 profile)，children 原样透传。
 *
 * 说明：T2.3 起 profile 在**渲染期**加载（而非 useEffect），保证子树（如
 * PanelSwitcher）首次渲染即可读到 profile 配置 —— 面板初始显隐（hidden）在
 * 首帧即正确，避免「先用回退默认值渲染、effect 后再校正」的瞬时闪动。
 * loadProfile 为幂等赋值，StrictMode 双渲染/HMR 均安全。
 *
 * profile.json 的外层 "slots" 是容器字段，而注册表以 slotId 为键，
 * 故此处展开 .slots 后加载（否则 getSlotEntries 读不到任何配置）。
 */
import React from 'react';
import { loadProfile } from './slotRegistry';
import defaultProfile from './profile.json';

export function SlotProvider({ children }: { children: React.ReactNode }) {
  loadProfile(defaultProfile.slots);
  return <>{children}</>;
}
