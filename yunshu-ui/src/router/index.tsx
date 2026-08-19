/**
 * 路由配置 —— HashRouter（兼容 Electron file:// 加载）
 * ------------------------------------------------
 * 结构：
 *   /login            → LoginLayout（空白布局）→ LoginPage
 *   / (受保护)         → RequireAuth（守卫）→ MainLayout → 配置驱动路由（appRoutes）
 *   /workbench        → 现有 Mosaic 工作台（迁移自 main.tsx 默认渲染）
 *   /prompt-lab        → 现有提示词实验室（保留原行为）
 *   /profile           → 现有登录流程测试页（保留原行为）
 *   /403              → 无权限页（AuthRoute 重定向进入）
 *   /detached/:panelId → Electron 独立窗口渲染进程（白名单校验）
 *
 * 守卫策略：
 *   - 登录守卫（RequireAuth）：localStorage 无 token 时重定向 /login，携带来源路径便于登录后跳回。
 *   - 权限守卫（AuthRoute）：包裹配置树每个节点，meta.authority 与用户角色不匹配时重定向 /403。
 *
 * 路由配置与菜单数据源见 src/router/routes.tsx。
 */
import {
  HashRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom'
import LoginLayout from '@/layouts/LoginLayout'
import MainLayout from '@/layouts/MainLayout'
import LoginPage from '@/pages/Login'
import PromptLab from '@/pages/PromptLab'
import Profile from '@/pages/Profile'
import ForbiddenPage from '@/pages/error/ForbiddenPage'
import { DetachedChatApp } from '@/DetachedChatApp'
import AuthRoute from '@/components/AuthRoute'
import { getToken } from '@/utils/request'
import { appRoutes, type AppRouteObject } from './routes'
import type { DetachablePanelId } from '@/electron/ipc'

/** 路由守卫：无 token 一律重定向登录页（state.from 记录来源，登录后跳回） */
function RequireAuth() {
  const location = useLocation()
  const token = getToken()
  if (!token) {
    console.warn(
      `[route-guard] RequireAuth：未登录（localStorage 无 token），${location.pathname}${location.search} → 重定向 /login（已携带 state.from 便于登录后跳回）`,
    )
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  console.info(
    `[route-guard] RequireAuth：已登录（token=${token.length > 8 ? token.slice(0, 8) + '…' : token}），放行 → ${location.pathname}${location.search}`,
  )
  return <Outlet />
}

/**
 * 将配置树渲染为 React Router 的 Route 节点。
 * - 分组节点（无 element）默认渲染 <Outlet/>，支持任意层级嵌套
 * - 每个节点统一包一层 AuthRoute 做权限守卫（含分组，权限向下继承）
 */
function renderRoutes(routes: AppRouteObject[]) {
  return routes.map((route) => {
    const element = route.element ?? <Outlet />
    return (
      <Route
        key={route.path ?? 'group'}
        path={route.path}
        element={<AuthRoute authority={route.meta?.authority}>{element}</AuthRoute>}
      >
        {route.children ? renderRoutes(route.children) : null}
      </Route>
    )
  })
}

/** Electron 独立窗口面板白名单（沿用 main.tsx 原有校验，非法面板回退主界面） */
const DETACHABLE_PANELS: readonly string[] = ['chat', 'think', 'nav', 'code']

function DetachedPanelRoute() {
  const { panelId } = useParams<{ panelId: string }>()
  if (!panelId || !DETACHABLE_PANELS.includes(panelId)) {
    console.warn(
      `[route-guard] DetachedPanelRoute：非法独立窗口面板 "${panelId ?? '(空)'}" → 重定向 /`,
    )
    return <Navigate to="/" replace />
  }
  console.info(`[route-guard] DetachedPanelRoute：白名单校验通过，渲染独立窗口面板 /detached/${panelId}`)
  return <DetachedChatApp panelId={panelId as DetachablePanelId} />
}

/** 兜底路由：未知路径统一回仪表盘（打日志便于排查拼写错误） */
function NotFoundRedirect() {
  const location = useLocation()
  console.warn(
    `[route-guard] 兜底路由：未知路径 ${location.pathname}${location.search} → 重定向 /`,
  )
  return <Navigate to="/" replace />
}

export default function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        {/* 登录页：空白布局 */}
        <Route path="/login" element={<LoginLayout />}>
          <Route index element={<LoginPage />} />
        </Route>

        {/* 受保护区域：登录守卫 + 主布局（Sidebar + Header + Outlet），路由由配置树驱动 */}
        <Route element={<RequireAuth />}>
          <Route element={<MainLayout />}>
            {renderRoutes(appRoutes)}
          </Route>
        </Route>

        {/* 既有独立页面：不套布局，保持原有渲染行为 */}
        <Route path="/prompt-lab" element={<PromptLab />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/detached/:panelId" element={<DetachedPanelRoute />} />

        {/* 403 无权限页：由 AuthRoute 重定向进入 */}
        <Route path="/403" element={<ForbiddenPage />} />

        {/* 兜底：未知路径回仪表盘 */}
        <Route path="*" element={<NotFoundRedirect />} />
      </Routes>
    </HashRouter>
  )
}
