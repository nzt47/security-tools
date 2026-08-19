/**
 * AuthRoute —— 路由级权限守卫
 * 包裹受保护的路由节点：meta.authority 与当前用户角色不匹配时重定向 403 页。
 * 无 authority 的路由视为公开（所有登录用户可访问）。
 */
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useUserStore } from '@/store/userStore'
import { hasAuthority } from '@/router/routes'

interface AuthRouteProps {
  /** 该路由所需权限码（来自 meta.authority），缺省表示公开 */
  authority?: string
  children: ReactNode
}

export default function AuthRoute({ authority, children }: AuthRouteProps) {
  const userInfo = useUserStore((s) => s.userInfo)
  const location = useLocation()

  const allowed = hasAuthority(authority, userInfo?.role, userInfo?.permissions)
  // 调试日志：输出当前路径、所需权限码、用户角色、权限集合与判定结果，便于排查 403 跳转链路
  console.log(
    `[权限·AuthRoute] 路径=${location.pathname}｜authority=${authority ?? '（无，公开路由）'}｜role=${userInfo?.role ?? '（空）'}｜permissions=${JSON.stringify(userInfo?.permissions ?? [])}｜判定=${allowed ? '放行' : '不匹配 → 重定向 /403'}`,
  )

  if (!allowed) {
    return <Navigate to="/403" replace state={{ from: location }} />
  }
  return <>{children}</>
}
