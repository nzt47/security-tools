/**
 * SlotHost —— 插槽渲染组件（任务 T2.1）
 *
 * <SlotHost slotId="sidebar" /> 读取该插槽条目（应用 profile 的 order/hidden），
 * 按序渲染全部组件；外层容器带 data-slot={slotId}。
 *
 * 每次渲染都实时读取注册表（getSlotEntries），无需额外订阅机制（单人工具够用）。
 */
import React from 'react';
import { getSlotEntries } from './slotRegistry';

export interface SlotHostProps {
  /** 插槽 id（稳定契约，见 PLAN-2 §5） */
  slotId: string;
  /** 外层容器 className */
  className?: string;
}

export function SlotHost({ slotId, className }: SlotHostProps) {
  const entries = getSlotEntries(slotId);
  return (
    <div className={className} data-slot={slotId}>
      {entries.map((e) => {
        const C = e.component;
        return <C key={e.id} />;
      })}
    </div>
  );
}
