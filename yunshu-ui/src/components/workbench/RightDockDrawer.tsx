/**
 * RightDockDrawer —— 右栏的**覆盖式 320px 抽屉**（2026-11-15 B 步）
 * ------------------------------------------------------------------
 * Owner 拍板：右栏默认收起、点开滑出。本轮**不换布局壳**（mosaic 保留，拖拽拆分与
 * 拖到边缘分离独立窗口都不受影响），只把右栏从"常驻 13% 的 mosaic 子格"换成
 * "浮在内容上的抽屉"：
 *   - 收起时不占任何宽度（主内容吃满）—— 1440 屏等于多出 ≈187px；
 *   - 打开时从右侧滑出，320px，盖住主内容右缘（不挤压 ⇒ 不触发 mosaic 重排）；
 *   - 内容仍是 renderPanel(PANEL.CODE)（右坞：编辑器/文档/子代理/浏览器/终端），
 *     与主工作台、独立窗口共用同一个映射，不另做一套。
 *
 * 开合状态住在 useLayoutStore.rightDockOpen（持久化）；本组件只负责呈现与关闭。
 */
import { useEffect } from 'react';
import { X } from 'lucide-react';
import { PANEL } from '../../lib/mosaic';
import { renderPanel } from './panels/renderPanel';

interface Props {
  open: boolean;
  onClose: () => void;
}

/** 抽屉宽度（px）：Owner 指定 320；窄屏下用 CSS max-width 兜底 */
export const RIGHT_DOCK_WIDTH = 320;

export function RightDockDrawer({ open, onClose }: Props) {
  // Esc 关闭：与其它抽屉/弹窗同一习惯（只在本抽屉打开时挂监听）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <aside
      className="wb-right-drawer"
      role="dialog"
      aria-label="右栏"
      data-testid="wb-right-drawer"
      style={{ width: RIGHT_DOCK_WIDTH }}
    >
      {/* 头部**不放标题文字**（Owner 2026-11-15 二次回调：面板内部也不出现「右栏」字样），
          只留一颗靠右的关闭按钮；可访问名仍由 aside 的 aria-label="右栏" 提供。 */}
      <div className="wb-right-drawer-head">
        <button
          type="button"
          className="wb-right-drawer-close"
          aria-label="关闭右栏"
          title="关闭右栏（Esc）"
          onClick={onClose}
        >
          <X size={12} />
        </button>
      </div>
      <div className="wb-right-drawer-body">{renderPanel(PANEL.CODE)}</div>
    </aside>
  );
}
