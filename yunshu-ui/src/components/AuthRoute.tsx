/**
 * AuthRoute —— 路由级守卫（后端菜单树驱动）
 * 包裹受保护的路由节点，判定当前路径是否在后端下发菜单的可达集合内：
 *   - 未登录（localStorage 无 token）→ 重定向登录页
 *   - 路径不在已下发菜单中 → 重定向 403（后端未授权的页面，前端不渲染）
 * 菜单未加载完成时不渲染（正常流程由 MainLayout 骨架屏挡在前面，此处仅防御）。
 */
import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useUserStore } from '@/store/userStore'
import { getToken } from '@/utils/request'
import { flattenMenuPaths } from '@/router/menus'

interface AuthRouteProps {
  children: ReactNode
}

export default function AuthRoute({ children }: AuthRouteProps) {
  const menus = useUserStore((s) => s.menus)
  const location = useLocation()

  // 菜单树未加载完成：可达集合为空，防御性不渲染（MainLayout 骨架屏期间本组件不会出现）
  const reachable = useMemo(() => (menus ? flattenMenuPaths(menus) : null), [menus])

  // 未登录（localStorage 无 token）时优先回登录页：
  // 【Why】退出登录会同步清空凭证，若仍停留在受保护路由，此处若不拦截将重定向 403；
  // 先于权限判定返回登录页，保证"退出登录 → 登录页"的直观预期。
  if (!getToken()) {
    console.log(`[权限·AuthRoute] 未登录，路径=${location.pathname} → 重定向 /login`)
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  if (!reachable) {
    return null
  }

  // 后端未下发该路径（无权访问）→ 403
  const allowed = reachable.has(location.pathname)
  console.log(
    `[权限·AuthRoute] 路径=${location.pathname}｜后端下发可达路径=${JSON.stringify([...reachable])}｜判定=${allowed ? '放行' : '未下发 → 重定向 /403'}`,
  )

  if (!allowed) {
    return <Navigate to="/403" replace state={{ from: location }} />
  }
  return <>{children}</>
}
