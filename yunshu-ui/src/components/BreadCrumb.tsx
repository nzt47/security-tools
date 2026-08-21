/**
 * BreadCrumb —— 面包屑（后端菜单树驱动）
 * 依据当前路由路径，从 userStore.menus（后端下发）匹配每一级 title 生成层级导航。
 * 不在菜单内的路径（如 /workbench 未下发时）不渲染面包屑。
 */
import { useMemo } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useUserStore } from '@/store/userStore'
import { flattenRoutes } from '@/router/routes'
import { menuTreeToAppRoutes } from '@/router/menus'

export default function BreadCrumb() {
  const { pathname } = useLocation()
  const storeMenus = useUserStore((s) => s.menus)

  const crumbs = useMemo(() => {
    const menus = storeMenus ? menuTreeToAppRoutes(storeMenus) : []
    return flattenRoutes(menus)
      // 匹配当前路径的所有前缀路由（'/' 需单独处理，避免匹配到所有路径）
      .filter(
        (route) =>
          pathname === route.path ||
          (route.path !== '/' && pathname.startsWith(`${route.path}/`)),
      )
      // 路径短的在前 → 父级在前
      .sort((a, b) => a.path.length - b.path.length)
  }, [pathname, storeMenus])

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
