/**
 * SlotProvider —— 注入默认 profile（任务 T2.1）
 *
 * 挂载时 loadProfile(默认 profile)，children 原样透传。
 * 未接入 App 前由测试/后续任务使用；多次挂载以最后一次加载为准。
 *
 * 说明：profile.json 的外层 "slots" 是容器字段，而注册表以 slotId 为键，
 * 故此处展开 .slots 后加载（否则 getSlotEntries 读不到任何配置）。
 */
import React, { useEffect } from 'react';
import { loadProfile } from './slotRegistry';
import defaultProfile from './profile.json';

export function SlotProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    loadProfile(defaultProfile.slots);
  }, []);
  return <>{children}</>;
}
