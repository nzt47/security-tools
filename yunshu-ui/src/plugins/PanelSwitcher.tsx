/**
 * PanelSwitcher —— 面板切换器（任务 T2.3）
 *
 * - 读取 panels 插槽条目（应用 profile 的 hidden/order）；
 * - 渲染每个面板的开关按钮（title/icon），点击切换显隐
 *   （状态用 zustand panelsStore.open，默认按 profile hidden 初始化）；
 * - 渲染当前打开的 <PanelHost/>：遍历打开的条目渲染组件。
 *
 * 语义（与 PLAN-2 §3 / 验收项对齐）：
 * - hidden:true 表示「默认关闭」（按钮仍显示，初始 open=false）；
 * - profile.json 的 panels 数组是按钮清单：从数组移除某条目 →
 *   切换器不再显示该按钮（由 getManifestEntries 实现）；
 * - profile 未配置 panels 时回退全部已挂载条目。
 */
import React, { useEffect } from 'react';
import { getManifestEntries } from './slotRegistry';
import { usePanelsStore } from './panelsStore';
import { PanelHost, PANELS_SLOT_ID } from './PanelHost';
import './panels.css';

export function PanelSwitcher() {
  const entries = getManifestEntries(PANELS_SLOT_ID);
  const open = usePanelsStore((s) => s.open);
  const toggle = usePanelsStore((s) => s.toggle);

  // 初始 open 按 profile hidden 初始化；仅当条目集合（id+hidden）变化时重建，
  // 从而在 SlotProvider 加载 profile 后（首次渲染时 profile 尚未加载）自动校正。
  const initKey = entries.map((e) => `${e.id}:${e.hidden ? 1 : 0}`).join('|');
  useEffect(() => {
    usePanelsStore.getState().initOpen(entries, initKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initKey]);

  return (
    <div className="panel-switcher" data-panel-switcher>
      <div className="panel-switcher-buttons">
        {entries.map((e) => (
          <button
            key={e.id}
            type="button"
            className={`panel-switcher-btn ${open[e.id] ? 'active' : ''}`}
            onClick={() => toggle(e.id)}
            title={e.title ?? e.id}
            aria-label={e.title ?? e.id}
            data-panel-btn={e.id}
          >
            {e.icon && <span className="panel-switcher-icon">{e.icon}</span>}
            <span className="panel-switcher-label">{e.title ?? e.id}</span>
          </button>
        ))}
      </div>
      <PanelHost slotId={PANELS_SLOT_ID} />
    </div>
  );
}
