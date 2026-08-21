/**
 * 后端菜单树 → 前端渲染 转换工具
 * ------------------------------------------------------
 * 方案 2「后端下发菜单树」：
 *   - 菜单结构 / 可见性由后端 /api/auth/menus 按角色过滤后返回
 *   - 前端将后端节点（icon 为字符串）转换为 AppRouteObject（icon 为组件），
 *     复用 Sidebar / BreadCrumb 的现有递归渲染与 flattenRoutes
 *   - AuthRoute 用 flattenMenuPaths 判定当前路径是否在后端下发集合内
 */
import type { LucideIcon } from 'lucide-react'
import {
  Bell,
  FileDown,
  History,
  LayoutDashboard,
  ListTree,
  Palette,
  ScrollText,
  Settings,
  ShieldCheck,
  Users,
  Workflow,
} from 'lucide-react'
import type { MenuTreeNode } from '@/api/user'
import { flattenRoutes, type AppRouteObject } from './routes'

/** 后端 icon 名称 → lucide 图标组件 映射（新增图标在此登记） */
export const MENU_ICON_MAP: Record<string, LucideIcon> = {
  dashboard: LayoutDashboard,
  workbench: Workflow,
  demo: Palette,
  export: FileDown,
  system: Settings,
  user: Users,
  role: ShieldCheck,
  menu: ListTree,
  audit: History,
  notification: Bell,
  log: ScrollText,
}

/** 后端菜单树 → 前端菜单树（icon 字符串映射为组件，便于复用递归渲染） */
export function menuTreeToAppRoutes(menus: MenuTreeNode[]): AppRouteObject[] {
  return menus.map((node) => ({
    path: node.path,
    meta: {
      title: node.title,
      icon: node.icon ? MENU_ICON_MAP[node.icon] : undefined,
      authority: node.authority,
    },
    children: node.children ? menuTreeToAppRoutes(node.children) : undefined,
  }))
}

/** 后端菜单树的可达路径集合（含分组路径），供 AuthRoute 判定当前路径是否被后端下发 */
export function flattenMenuPaths(menus: MenuTreeNode[]): Set<string> {
  return new Set(flattenRoutes(menuTreeToAppRoutes(menus)).map((r) => r.path))
}
