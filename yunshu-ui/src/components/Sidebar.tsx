/**
 * Sidebar —— 侧边栏菜单（数据驱动）
 * 数据源：src/router/routes.tsx 的 appRoutes 配置树。
 * 渲染前按当前用户角色过滤：hideInMenu 剔除 + authority 权限校验 + 空分组剔除。
 * 递归渲染支持多级菜单；选中 / 悬停态使用 Tailwind。
 */
import { useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { Cloud } from 'lucide-react'
import { useUserStore } from '@/store/userStore'
import { appRoutes, filterMenus, type AppRouteObject } from '@/router/routes'

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
  const userInfo = useUserStore((s) => s.userInfo)
  const role = userInfo?.role
  const permissions = userInfo?.permissions

  // 角色/权限变化时重新过滤菜单（如切换账号后即时刷新可见项）
  const menus = useMemo(() => {
    console.log('========== [权限·Sidebar] 菜单过滤开始 ==========')
    console.log('[权限·Sidebar] 当前用户 role =', role ?? '（空）', '，permissions =', permissions ?? [])
    const result = filterMenus(appRoutes, role, permissions ?? [])
    console.log(
      '[权限·Sidebar] 过滤后菜单 =',
      result.map((r) => r.meta?.title ?? r.path ?? '无名'),
    )
    return result
  }, [role, permissions])

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
