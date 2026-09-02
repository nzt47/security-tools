/**
 * DetachButton —— 面板工具条"弹出到独立窗口"按钮
 * ------------------------------------------------
 * 点击 → onDetached 回调（上层统一执行 IPC detach-panel 建窗 + 从主窗口布局摘除面板）。
 * 注意：不在本组件内调用 window.electronAPI.detachPanel，否则会与上层的
 *       detachPanel（含 IPC）重复执行，导致弹出两个相同窗口。
 * Web 环境（window.electronAPI 不存在）不渲染，纯 Web 行为不变。
 */
import { ExternalLink } from 'lucide-react';
import { isElectron } from '../../electron/types';
import type { PanelId } from '../../lib/mosaic';

interface Props {
  panelId: PanelId;
  /** 分离回调：由上层执行 IPC 建窗并移除面板 */
  onDetached: (panelId: PanelId) => void;
}

export function DetachButton({ panelId, onDetached }: Props) {
  if (!isElectron()) return null;

  const handleDetach = () => {
    onDetached(panelId);
  };

  return (
    <button
      type="button"
      className="mosaic-default-control detach-button"
      title="弹出到独立窗口"
      onClick={() => void handleDetach()}
    >
      <ExternalLink size={11} />
      独立窗口
    </button>
  );
}
