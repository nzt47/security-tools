/**
 * 路由配置 —— HashRouter（兼容 Electron file:// 加载）
 * ------------------------------------------------
 * 结构（2026-09-01 统一路由合并，legacy/workbench/管理后台单一路由树）：
 *   /                → legacy 聊天主界面（App.tsx，公开，无需登录）
 *   /workbench       → 云枢 Mosaic 工作台（公开）
 *   /prompt-lab      → 提示词影响因素实验室（公开）
 *   /login           → 管理后台登录页（LoginLayout 空白布局）
 *   / (受保护)        → RequireAuth → MainLayout → 配置驱动路由（appRoutes：
 *                       /dashboard 仪表盘、/demo、/export、/system/*；知识库见工作台）
 *   /profile         → 登录流程测试页（保留原行为）
 *   /403             → 无权限页（AuthRoute 重定向进入）
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
import { lazy, Suspense } from 'react'
// 首屏：统一工作台（Mosaic），静态加载保证秒开
import WorkbenchApp from '@/WorkbenchApp'
// 归档/次要页面：懒加载（Code Splitting）
const App = lazy(() => import('@/App'))
const PromptLab = lazy(() => import('@/pages/prompt-lab'))
const LoginPage = lazy(() => import('@/pages/Login'))
const Profile = lazy(() => import('@/pages/Profile'))
import LoginLayout from '@/layouts/LoginLayout'
import MainLayout from '@/layouts/MainLayout'
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
 * - 每个节点统一包一层 AuthRoute 做访问守卫（后端菜单树驱动：路径未下发 → 403）
 */
function renderRoutes(routes: AppRouteObject[]) {
  return routes.map((route) => {
    const element = route.element ?? <Outlet />
    return (
      <Route
        key={route.path ?? 'group'}
        path={route.path}
        element={<AuthRoute>{element}</AuthRoute>}
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

/** 兜底路由：未知路径统一回 legacy 聊天主界面（打日志便于排查拼写错误） */
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
      <Suspense fallback={<div className="flex h-screen items-center justify-center bg-slate-950 text-sm text-slate-500">加载中…</div>}>
        <Routes>
          {/* ═══ 统一工作台（唯一主入口）═══
              所有功能集中在 /workbench（Mosaic 工作台：导航/主内容/思考/代码）。
              旧入口（legacy 聊天 / Hub 8 栏目 / 管理后台独立路由）一律归档重定向到工作台。 */}
        <Route path="/" element={<Navigate to="/workbench" replace />} />
        <Route path="/workbench" element={<WorkbenchApp />} />
        <Route path="/prompt-lab" element={<PromptLab />} />

        {/* 归档入口：/hub/* 重定向到统一工作台（保持旧链接可用） */}
        <Route path="/hub/*" element={<Navigate to="/workbench" replace />} />

        {/* 登录页：空白布局（管理后台登录，供工作台内系统管理使用） */}
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
        <Route path="/profile" element={<Profile />} />
        <Route path="/detached/:panelId" element={<DetachedPanelRoute />} />

        {/* 403 无权限页：由 AuthRoute 重定向进入 */}
        <Route path="/403" element={<ForbiddenPage />} />

        {/* 兜底：未知路径回 legacy 聊天主界面 */}
        <Route path="*" element={<NotFoundRedirect />} />
        </Routes>
      </Suspense>
    </HashRouter>
  )
}
