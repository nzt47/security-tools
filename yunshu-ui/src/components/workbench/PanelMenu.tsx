/**
 * PanelMenu —— 面板右上角的悬浮 ⋮ 菜单（2026-11-15 B 步）
 * ------------------------------------------------------------------
 * 【为什么从工具条搬到悬浮菜单】Owner：界面 chrome 继续瘦身到 DSH 观感 —— 去面板标题栏。
 * 标题栏去掉之后，原来那三个按钮（独立窗口 / 放大 / 关闭）如果还留在 6px 抓取条里
 * 就必然把抓取条撑回 24px 以上。做法：抓取条只管拖拽，三动作收进**面板右上角的悬浮 ⋮**。
 *
 * 【不易】三动作的语义与库默认按钮**逐一对齐**（都走 Mosaic 的 actions，不自己改布局树）：
 *   放大 = mosaicActions.expand(path)      —— 与 ExpandButton 同一实现
 *   关闭 = mosaicActions.remove(path)      —— 与 RemoveButton 同一实现（onChange 会回写 store）
 *   独立窗口 = 上层 detachPanel（IPC 建窗 + 从布局摘除）—— 与原来的 DetachButton 同一回调
 * aria-label 沿用既有命名（"独立窗口" / "放大" / "关闭"），测试与读屏都按名字找。
 *
 * 【为什么不放进抓取条里】抓取条是 react-dnd 的 drag source（MosaicWindow 把自定义 toolbar
 * 包成 drag source）；把可点按钮塞进 drag source 会让"点按钮"与"拖面板"抢同一个 mousedown。
 * 本组件渲染在面板 body 里，天然在 drag source 之外。
 */
import { useEffect, useRef, useState } from 'react';
import { useContext } from 'react';
import { EllipsisVertical, Maximize2, X } from 'lucide-react';
import { MosaicContext, MosaicWindowContext } from 'react-mosaic-component';
import type { PanelId } from '../../lib/mosaic';
import { PANEL_TITLES } from '../../lib/mosaic';
import { DetachButton } from './DetachButton';

interface Props {
  panelId: PanelId;
  /** 分离为独立窗口（Electron）；上层已含 IPC + 从布局摘除 */
  onDetach: (panelId: PanelId) => void;
}

export function PanelMenu({ panelId, onDetach }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { mosaicActions } = useContext(MosaicContext);
  const { mosaicWindowActions } = useContext(MosaicWindowContext);
  const title = PANEL_TITLES[panelId];

  // 菜单开着时：点外面/按 Esc 收起（与工作台其它上下文菜单同一套习惯）
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const expand = () => {
    setOpen(false);
    mosaicActions.expand(mosaicWindowActions.getPath());
  };

  const close = () => {
    setOpen(false);
    mosaicActions.remove(mosaicWindowActions.getPath());
  };

  return (
    <div className="wb-panel-menu" ref={rootRef}>
      <button
        type="button"
        className="wb-panel-menu-trigger"
        aria-label={`${title} 面板菜单`}
        aria-haspopup="menu"
        aria-expanded={open}
        title="面板菜单：独立窗口 / 放大 / 关闭"
        data-testid="wb-panel-menu-trigger"
        onClick={() => setOpen((v) => !v)}
      >
        <EllipsisVertical size={12} />
      </button>
      {open && (
        <div className="wb-panel-menu-pop" role="menu" aria-label={`${title} 面板操作`}>
          {/* 独立窗口：Web 模式没有 window.electronAPI，DetachButton 会返回 null（既有行为） */}
          <DetachButton
            panelId={panelId}
            onDetached={(target) => {
              setOpen(false); // 菜单动作一律点了就收起（与本组件的另外两项一致）
              onDetach(target);
            }}
            className="wb-panel-menu-item"
            ariaLabel="独立窗口"
          />
          <button
            type="button"
            role="menuitem"
            aria-label="放大"
            className="wb-panel-menu-item"
            onClick={expand}
          >
            <Maximize2 size={11} />
            放大
          </button>
          <button
            type="button"
            role="menuitem"
            aria-label="关闭"
            className="wb-panel-menu-item danger"
            onClick={close}
          >
            <X size={11} />
            关闭
          </button>
        </div>
      )}
    </div>
  );
}
