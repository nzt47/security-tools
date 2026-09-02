import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import AppRouter from './router'
import './index.css'

/**
 * 入口（2026-09-01 统一路由合并）：
 * 单一 HashRouter（AppRouter）管理全部前端入口，不再手工分发 hash：
 * - `/`            → legacy 聊天主界面（App.tsx，插件插槽驱动）
 * - `/workbench`   → 云枢 Mosaic 工作台（多面板拖拽/拆分）
 * - `/prompt-lab`  → 提示词影响因素实验室
 * - `/login`       → 管理后台登录页
 * - `/dashboard`   → 管理后台仪表盘（登录守卫 + 权限）
 * - `/system/*`    → 管理后台系统管理（用户/角色/菜单/审计/消息/日志）
 * - `/detached/:panelId` → Electron 独立窗口视图
 *
 * 守卫策略：管理后台区域 RequireAuth（无 token 跳 /login）；
 * legacy / workbench / prompt-lab 为公开页面，无需登录。
 */
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppRouter />
  </StrictMode>,
)
