/**
 * DetachButton —— 面板"弹出到独立窗口"按钮
 * ------------------------------------------------
 * 点击 → onDetached 回调（上层统一执行 IPC detach-panel 建窗 + 从主窗口布局摘除面板）。
 * 注意：不在本组件内调用 window.electronAPI.detachPanel，否则会与上层的
 *       detachPanel（含 IPC）重复执行，导致弹出两个相同窗口。
 * Web 环境（window.electronAPI 不存在）不渲染，纯 Web 行为不变。
 *
 * 2026-11-15（B 步）：面板标题栏去掉后，本按钮的宿主从工具条换成右上角悬浮 ⋮ 菜单
 * （见 PanelMenu）。因此把样式类与 aria-label 做成可传参：默认值仍是工具条形态，
 * 菜单里传 wb-panel-menu-item + "独立窗口"，**不改动作语义**。
 */
import { ExternalLink } from 'lucide-react';
import { isElectron } from '../../electron/types';
import type { PanelId } from '../../lib/mosaic';

interface Props {
  panelId: PanelId;
  /** 分离回调：由上层执行 IPC 建窗并移除面板 */
  onDetached: (panelId: PanelId) => void;
  /** 样式类（默认工具条形态；⋮ 菜单里传 wb-panel-menu-item） */
  className?: string;
  /** 可访问名（默认"独立窗口"） */
  ariaLabel?: string;
}

export function DetachButton({
  panelId,
  onDetached,
  className = 'mosaic-default-control detach-button',
  ariaLabel = '独立窗口',
}: Props) {
  if (!isElectron()) return null;

  const handleDetach = () => {
    onDetached(panelId);
  };

  return (
    <button
      type="button"
      className={className}
      title="弹出到独立窗口"
      aria-label={ariaLabel}
      onClick={() => void handleDetach()}
    >
      <ExternalLink size={11} />
      独立窗口
    </button>
  );
}
