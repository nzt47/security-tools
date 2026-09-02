/**
 * 路由配置 —— HashRouter（兼容 Electron file:// 加载）
 * ------------------------------------------------
 * 结构（统一路由收敛，第二套管理后台外壳已摘除）：
 *   /                  → 重定向统一工作台 /workbench（保留 '/' 空地址兜底）
 *   /workbench         → 云枢统一工作台（唯一主入口：Mosaic 导航 + 主内容区；
 *                        系统管理 / 数据导出等全部收敛到「系统管理」栏目，见 workbench/hubNav.tsx）
 *   /prompt-lab        → 提示词影响因素实验室（公开）
 *   /login             → 登录页（LoginLayout 空白布局）
 *   /profile           → 登录流程测试页（保留原行为）
 *   /403               → 无权限页（保留原行为，手输 URL 可访问）
 *   /detached/:panelId → Electron 独立窗口渲染进程（白名单校验）
 *   /hub/*             → 归档入口：统一重定向 /workbench（保持旧链接可用）
 *   *（其他任意旧路径，如 /dashboard、/demo、/export、/system/*）→ 兜底重定向 /workbench，不再白屏
 *
 * 重构说明（摘除第二套管理后台外壳）：
 *   - 原「受保护区域」（RequireAuth → MainLayout → 配置驱动路由 appRoutes：
 *     /dashboard /demo /export /system/*，见旧 src/router/routes.tsx）全站无入口链接，
 *     需登录 + 后端菜单命中 + 手输 URL，实际是"影子后台"，已整体摘除。
 *   - 其页面组件（pages/system/*、pages/Dashboard、pages/Export）保留不删，
 *     全部收敛到工作台 hubNav「admin」分组（lazy 挂载，复用同一批页面组件）。
 *   - /login 保留：管理后台页面依赖 localStorage token（axios 401 拦截器跳 #/login），
 *     工作台系统管理栏目的 AdminGuard 也提供「去登录」入口。
 *   - 登录后落地页 = /（重定向 /workbench），已是默认行为。
 */
import {
  HashRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from 'react-router-dom'
import { lazy, Suspense } from 'react'
// 首屏：统一工作台（Mosaic），静态加载保证秒开
import WorkbenchApp from '@/WorkbenchApp'
// 归档/次要页面：懒加载（Code Splitting）
const PromptLab = lazy(() => import('@/pages/prompt-lab'))
const LoginPage = lazy(() => import('@/pages/Login'))
const Profile = lazy(() => import('@/pages/Profile'))
import LoginLayout from '@/layouts/LoginLayout'
import ForbiddenPage from '@/pages/error/ForbiddenPage'
import { DetachedChatApp } from '@/DetachedChatApp'
import { DETACHABLE_PANELS, type DetachablePanelId } from '@/electron/ipc'

/**
 * Electron 独立窗口面板白名单。
 * 【单一来源】值取自 electron/ipc.ts 的 DETACHABLE_PANELS（缺陷 ③：此前本文件
 * 与 ipc.ts 双处硬编码 ['chat','think','nav','code']，改动易漂移），非法面板回退主界面。
 */
const DETACHABLE_PANEL_VALUES: readonly string[] = Object.values(DETACHABLE_PANELS)

function DetachedPanelRoute() {
  const { panelId } = useParams<{ panelId: string }>()
  if (!panelId || !DETACHABLE_PANEL_VALUES.includes(panelId)) {
    console.warn(
      `[route-guard] DetachedPanelRoute：非法独立窗口面板 "${panelId ?? '(空)'}" → 重定向 /`,
    )
    return <Navigate to="/" replace />
  }
  console.info(`[route-guard] DetachedPanelRoute：白名单校验通过，渲染独立窗口面板 /detached/${panelId}`)
  return <DetachedChatApp panelId={panelId as DetachablePanelId} />
}

/** 兜底路由：未知路径（含已下线的旧管理后台 /dashboard、/system/* 等）统一回统一工作台 */
function NotFoundRedirect() {
  const location = useLocation()
  console.warn(
    `[route-guard] 兜底路由：未知路径 ${location.pathname}${location.search} → 重定向 /workbench`,
  )
  return <Navigate to="/workbench" replace />
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

          {/* 既有独立页面：不套布局，保持原有渲染行为 */}
          <Route path="/profile" element={<Profile />} />
          <Route path="/detached/:panelId" element={<DetachedPanelRoute />} />

          {/* 403 无权限页：保留原行为（手输 URL 可访问） */}
          <Route path="/403" element={<ForbiddenPage />} />

          {/* 兜底：未知路径（含已下线管理后台旧路径）回统一工作台，避免白屏 */}
          <Route path="*" element={<NotFoundRedirect />} />
        </Routes>
      </Suspense>
    </HashRouter>
  )
}
