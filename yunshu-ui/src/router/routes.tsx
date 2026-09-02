/**
 * 路由/权限工具 —— 类型定义与纯函数（无任何页面组件）
 * ------------------------------------------------------
 * 历史：本文件原为「管理后台第二套外壳」的路由配置中心（appRoutes：
 * /dashboard /demo /export /system/*，驱动 MainLayout/Sidebar/BreadCrumb/AuthRoute）。
 * 该外壳已整体摘除（见 src/router/index.tsx 顶部说明），其页面组件收敛到统一工作台
 * hubNav「admin」分组（workbench/hubNav.tsx，lazy 挂载）。
 *
 * 现保留纯工具部分供权限判定复用：
 *   - RouteMeta / AppRouteObject / FlattenedRoute：通用路由/菜单节点类型
 *   - flattenRoutes：嵌套树拍平
 *   - hasAuthority / filterMenus：权限码集合模型（admin 通配 / permissions 命中），
 *     与后端 PermissionManager.has_permission 语义一致；按钮级场景直接用
 *     src/hooks/usePermission.ts（内联同款判定，避免循环依赖）。
 */
import type { ReactNode } from 'react'

/** 路由元信息（驱动菜单、面包屑、权限控制） */
export interface RouteMeta {
  /** 菜单名 / 面包屑文案 */
  title: string
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
