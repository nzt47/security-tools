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
import { DockPanel } from './DockPanel'

/**
 * 【2026-10-31】右栏由"固定代码编辑器"升级为**可切换右坞**（编辑器/文档/子代理/浏览器/终端）。
 * 面板 id 仍是 `code` ⇒ 已持久化的历史布局无需迁移；默认 Tab 仍是编辑器，
 * 所以"只想用编辑器"的用户观感不变。
 */
export function renderPanel(id: PanelId): ReactElement {
  switch (id) {
    case PANEL.NAV:
      return <NavPanel />
    case PANEL.CODE:
      return <DockPanel />
    case PANEL.CHAT:
    default:
      return <ContentPanel />
  }
}
