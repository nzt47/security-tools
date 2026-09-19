/**
 * renderPanel —— 工作台"面板 ID → 组件"统一映射
 * ------------------------------------------------
 * 主工作台（WorkbenchApp）与独立窗口视图（DetachedChatApp）共用本函数，
 * 保证"分离到独立窗口的面板"与主工作台渲染一致（缺陷 ③：此前独立窗口
 * CHAT→ChatPanel / NAV→SidebarPanel，与主工作台 CHAT→ContentPanel /
 * NAV→NavPanel 不一致，导致独立窗口内容偏离主工作台）。
 *
 * 变更面板映射时只改这里一处，两处入口同步生效。
 * 注：原 THINK（右侧「思考过程」）面板已下线 —— 思考/工具调用改为在每条回复内联显示。
 */
import type { ReactElement } from 'react'
import { PANEL, type PanelId } from '../../../lib/mosaic'
import { NavPanel } from './NavPanel'
import { ContentPanel } from './ContentPanel'
import { CodeEditorPanel } from './CodeEditorPanel'

/** 渲染指定面板组件（未知/兜底一律回退主内容面板） */
export function renderPanel(id: PanelId): ReactElement {
  switch (id) {
    case PANEL.NAV:
      return <NavPanel />
    case PANEL.CODE:
      return <CodeEditorPanel />
    case PANEL.CHAT:
    default:
      return <ContentPanel />
  }
}
