/**
 * PanelHost —— 面板容器（任务 T2.3）
 *
 * 读取 panelsStore 的 open 状态，遍历「打开的条目」渲染组件：
 * - 数据源：panels 插槽的 profile 清单（getManifestEntries，应用 order/hidden）；
 * - 每个面板组件自带呈现（内容面板用 PanelFrame 浮层框架，
 *   DevConsole 为自定位浮层 portal 到 body，无需框架）；
 * - 面板关闭时组件卸载（与改造前「关闭即卸载」行为一致；
 *   DevConsole 的 requestInterceptor 在挂载时启用、卸载时恢复）。
 */
import React from 'react';
import { getManifestEntries } from './slotRegistry';
import { usePanelsStore } from './panelsStore';

/** panels 插槽 id（稳定契约，见 PLAN-2 §5；PanelSwitcher 同此） */
export const PANELS_SLOT_ID = 'panels';

export interface PanelHostProps {
  /** 面板插槽 id，默认 'panels' */
  slotId?: string;
}

export function PanelHost({ slotId = PANELS_SLOT_ID }: PanelHostProps) {
  const open = usePanelsStore((s) => s.open);
  const entries = getManifestEntries(slotId);
  const openEntries = entries.filter((e) => open[e.id]);

  if (openEntries.length === 0) return null;

  return (
    <div className="panel-host" data-panel-host>
      {openEntries.map((e) => {
        const C = e.component;
        return <C key={e.id} />;
      })}
    </div>
  );
}
