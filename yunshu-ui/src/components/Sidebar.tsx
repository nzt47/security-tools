/**
 * Sidebar —— 侧边栏菜单（后端菜单树驱动）
 * 数据源：userStore.menus（后端 /api/auth/menus 按角色过滤下发）。
 * 前端将后端节点（icon 字符串）转换为 AppRouteObject 后递归渲染；
 * 菜单结构 / 可见性完全由后端控制，前端不再做权限过滤。
 */
import { useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { Cloud } from 'lucide-react'
import { useUserStore } from '@/store/userStore'
import { menuTreeToAppRoutes } from '@/router/menus'
import type { AppRouteObject } from '@/router/routes'

/** 单个菜单节点：分组 → 渲染分组标题 + 递归子项；叶子 → 渲染 NavLink */
function MenuNode({ route }: { route: AppRouteObject }) {
  const Icon = route.meta?.icon

  // 分组节点：非跳转链接，仅渲染分组标题 + 子菜单
  if (route.children && route.children.length > 0) {
    return (
      <div>
        <div className="flex items-center gap-2 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          {Icon && <Icon size={14} />}
          {route.meta?.title}
        </div>
        <div className="ml-2 space-y-1 border-l border-slate-100 pl-2">
          {route.children.map((child) => (
            <MenuNode key={child.path ?? child.meta?.title} route={child} />
          ))}
        </div>
      </div>
    )
  }

  // 叶子节点（无子菜单，缺 path 时兜底不渲染）
  if (!route.path) return null

  return (
    <NavLink
      to={route.path}
      end={route.path === '/'}
      className={({ isActive }) =>
        `flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
          isActive
            ? 'bg-blue-50 font-medium text-blue-600'
            : 'text-slate-600 hover:bg-slate-100'
        }`
      }
    >
      {Icon && <Icon size={16} />}
      {route.meta?.title}
    </NavLink>
  )
}

export default function Sidebar() {
  const role = useUserStore((s) => s.userInfo?.role)
  const storeMenus = useUserStore((s) => s.menus)

  // 后端菜单树 → 前端可渲染菜单（icon 字符串映射为 lucide 组件）
  const menus = useMemo(() => menuTreeToAppRoutes(storeMenus ?? []), [storeMenus])

  // 调试日志：后端下发菜单即为最终菜单（前端不再过滤，便于排查与旧逻辑对比）
  console.log(
    '[权限·Sidebar] 当前用户 role =',
    role ?? '（空）',
    '，后端下发菜单 =',
    menus.map((r) => r.meta?.title ?? r.path ?? '无名'),
  )

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2 border-b border-slate-200 px-4">
        <Cloud size={18} className="text-blue-600" />
        <span className="font-semibold text-slate-800">云枢工作台</span>
      </div>

      <nav className="flex-1 space-y-1 overflow-auto p-2">
        {menus.map((route) => (
          <MenuNode key={route.path ?? route.meta?.title} route={route} />
        ))}
      </nav>
    </aside>
  )
}
