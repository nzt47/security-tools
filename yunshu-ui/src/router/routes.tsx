/**
 * 路由配置中心 —— 菜单 / 面包屑 / 权限控制的单一数据源
 * ------------------------------------------------------
 * 每个节点承载三份职责：
 *   - path / element：交给 React Router 渲染
 *   - meta：驱动 Sidebar 菜单（title / icon / hideInMenu）与 BreadCrumb（title）
 *   - meta.authority：驱动权限控制（AuthRoute 守卫 + 菜单过滤），权限码如 'system:view'
 *
 * 约定：
 *   - 路径统一小写英文，如 /system/user
 *   - 无 children 的节点为叶子页；有 children 的节点为分组（默认渲染 <Outlet/>）
 *   - 不出现在菜单的路由（详情页等）置 hideInMenu: true
 */
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { FileDown, LayoutDashboard, ListTree, Palette, ScrollText, Settings, ShieldCheck, Users, Workflow } from 'lucide-react'
import Dashboard from '@/pages/Dashboard'
import WorkbenchApp from '@/WorkbenchApp'
import UserList from '@/pages/system/UserList'
import RoleList from '@/pages/system/RoleList'
import MenuList from '@/pages/system/MenuList'
import SystemLog from '@/pages/system/SystemLog'
import DemoPage from '@/pages/Demo'
import DataExport from '@/pages/Export'

/** 路由元信息（驱动菜单、面包屑、权限控制） */
export interface RouteMeta {
  /** 菜单名 / 面包屑文案 */
  title: string
  /** 菜单图标（lucide 图标组件），缺省不显示图标 */
  icon?: LucideIcon
  /** 权限码（如 'system:view'）：需命中 userInfo.permissions（admin 角色通配）；缺省表示登录用户均可见 */
  authority?: string
  /** 是否在侧边栏菜单隐藏（如详情页），默认 false */
  hideInMenu?: boolean
}

/** 应用路由配置节点（可嵌套形成多级菜单） */
export interface AppRouteObject {
  /** 路由路径（统一小写英文）；无 path 的节点仅作菜单分组 */
  path?: string
  /** 页面组件；缺省渲染 <Outlet/>（分组节点） */
  element?: ReactNode
  /** 路由元信息 */
  meta?: RouteMeta
  /** 子路由 */
  children?: AppRouteObject[]
}

/**
 * 应用路由配置（侧边栏菜单的单一数据源）
 * 权限模型：authority 为权限码，admin 角色通配；其余角色需命中 userInfo.permissions。
 * - 仪表盘/工作台/组件演示/数据导出：登录用户可见
 * - 系统管理：需 system:view（用户列表需 system:user:view，仅 admin 可见）
 */
export const appRoutes: AppRouteObject[] = [
  {
    path: '/',
    element: <Dashboard />,
    meta: { title: '仪表盘', icon: LayoutDashboard },
  },
  {
    path: '/workbench',
    element: <WorkbenchApp />,
    meta: { title: '工作台', icon: Workflow },
  },
  {
    path: '/demo',
    element: <DemoPage />,
    meta: { title: '组件演示', icon: Palette },
  },
  {
    path: '/export',
    element: <DataExport />,
    meta: { title: '数据导出', icon: FileDown },
  },
  {
    path: '/system',
    meta: { title: '系统管理', icon: Settings, authority: 'system:view' },
    children: [
      {
        path: '/system/user',
        element: <UserList />,
        meta: { title: '用户列表', icon: Users, authority: 'system:user:view' },
      },
      {
        path: '/system/role',
        element: <RoleList />,
        meta: { title: '角色权限', icon: ShieldCheck, authority: 'system:role:view' },
      },
      {
        path: '/system/menu',
        element: <MenuList />,
        meta: { title: '菜单管理', icon: ListTree, authority: 'system:role:view' },
      },
      {
        path: '/system/log',
        element: <SystemLog />,
        meta: { title: '系统日志', icon: ScrollText, authority: 'system:view' },
      },
    ],
  },
]

/** 拍平后的路由条目（用于面包屑路径匹配等场景） */
export interface FlattenedRoute {
  path: string
  meta?: RouteMeta
}

/** 将嵌套路由配置拍平为「路径 + 元信息」列表（纯分组节点会被跳过） */
export function flattenRoutes(routes: AppRouteObject[]): FlattenedRoute[] {
  return routes.flatMap((route) => {
    const self = route.path ? [{ path: route.path, meta: route.meta }] : []
    return route.children ? [...self, ...flattenRoutes(route.children)] : self
  })
}

/**
 * 权限判定（权限码集合模型）：
 * - 无权限码 → 公开路由
 * - admin 角色 → 通配（拥有全部权限）
 * - 其他角色 → 权限码需命中 userInfo.permissions 集合
 */
export function hasAuthority(
  authority: string | undefined,
  role: string | undefined,
  permissions: string[] = [],
): boolean {
  if (!authority) return true
  if (role === 'admin') return true
  return permissions.includes(authority)
}

/**
 * 按权限过滤菜单树（递归）
 * 1. 剔除 hideInMenu 节点
 * 2. 剔除权限不匹配的节点
 * 3. 分组内无可见子项时整个分组隐藏
 *
 * 内部输出 console.log 日志（[权限·filterMenus] 前缀），便于排查菜单显隐的判定流程；
 * 仅作调试用途，不影响过滤结果。
 */
export function filterMenus(
  routes: AppRouteObject[],
  role?: string,
  permissions: string[] = [],
): AppRouteObject[] {
  console.log(
    '[权限·filterMenus] 入参 role =',
    role ?? '（空）',
    '，permissions =',
    permissions,
    '｜ 待过滤路由 =',
    routes.map((r) => r.meta?.title ?? r.path ?? '无名'),
  )

  const result = routes
    .filter((route) => {
      if (route.meta?.hideInMenu) {
        console.log(`[权限·filterMenus] 剔除：${route.meta.title}（hideInMenu: true）`)
        return false
      }
      return true
    })
    .filter((route) => {
      const authority = route.meta?.authority
      const allowed = hasAuthority(authority, role, permissions)
      if (!allowed) {
        console.log(
          `[权限·filterMenus] 剔除：${route.meta?.title ?? route.path ?? '无名'}（authority=${authority}，permissions=${JSON.stringify(permissions)}，未命中）`,
        )
      }
      return allowed
    })
    .map((route) => ({
      ...route,
      children: route.children ? filterMenus(route.children, role, permissions) : undefined,
    }))
    .filter((route) => {
      // 分组下所有子项均被剔除时，整个分组一并隐藏
      if (route.children && route.children.length === 0) {
        console.log(
          `[权限·filterMenus] 剔除空分组：${route.meta?.title ?? route.path ?? '无名'}（子项全部不可见）`,
        )
        return false
      }
      return true
    })

  console.log(
    '[权限·filterMenus] 过滤完成，保留 =',
    result.map((r) => r.meta?.title ?? r.path ?? '无名'),
  )
  return result
}
