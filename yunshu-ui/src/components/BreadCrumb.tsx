/**
 * BreadCrumb —— 面包屑
 * 依据当前路由路径，从路由配置（appRoutes）中匹配每一级 title 生成层级导航。
 * 不在菜单配置内的路径（如 /workbench）不渲染面包屑。
 */
import { useMemo } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { appRoutes, flattenRoutes } from '@/router/routes'

export default function BreadCrumb() {
  const { pathname } = useLocation()

  const crumbs = useMemo(
    () =>
      flattenRoutes(appRoutes)
        // 匹配当前路径的所有前缀路由（'/' 需单独处理，避免匹配到所有路径）
        .filter(
          (route) =>
            pathname === route.path ||
            (route.path !== '/' && pathname.startsWith(`${route.path}/`)),
        )
        // 路径短的在前 → 父级在前
        .sort((a, b) => a.path.length - b.path.length),
    [pathname],
  )

  if (crumbs.length === 0) return null

  return (
    <nav aria-label="面包屑" className="flex items-center gap-1 text-sm">
      {crumbs.map((crumb, index) => {
        const isLast = index === crumbs.length - 1
        return (
          <span key={crumb.path} className="flex items-center gap-1">
            {index > 0 && <span className="text-slate-300">/</span>}
            {isLast ? (
              <span className="font-medium text-slate-700">{crumb.meta?.title}</span>
            ) : (
              <Link
                to={crumb.path}
                className="text-slate-500 transition-colors hover:text-blue-600"
              >
                {crumb.meta?.title}
              </Link>
            )}
          </span>
        )
      })}
    </nav>
  )
}
