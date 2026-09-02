import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import AppRouter from './router'
import './index.css'

/**
 * 入口（统一路由收敛：摘除第二套管理后台外壳后）：
 * 单一 HashRouter（AppRouter）管理全部前端入口：
 * - `/`            → 重定向统一工作台
 * - `/workbench`   → 云枢 Mosaic 工作台（唯一主入口；系统管理/数据导出等
 *                    收敛到「系统管理」栏目，见 workbench/hubNav.tsx）
 * - `/prompt-lab`  → 提示词影响因素实验室
 * - `/login`       → 登录页（管理页面 401 拦截器 / AdminGuard 跳转而来）
 * - `/profile`     → 登录流程测试页
 * - `/403`         → 无权限页
 * - `/detached/:panelId` → Electron 独立窗口视图
 * - 旧管理后台路径（/dashboard、/system/*、/export…）经兜底重定向回 /workbench
 *
 * 守卫策略：旧 RequireAuth/MainLayout 管理后台外壳已移除；
 * 工作台内管理页面经 axios 401 拦截器（跳 #/login）与 AdminGuard 维持登录态约束。
 */
import { installMockElectron } from './electron/mockElectron'

// ─── Electron API Mock（缺陷 ③：installMockElectron 此前无调用方，文档所称
// "main.tsx 自动注入"未接线）───────────────────────────────
// 仅开发环境生效：$env:VITE_MOCK_ELECTRON="1"; npm run dev
// → 注入 window.electronAPI（mock），工作台面板工具条出现"独立窗口"按钮，
// 可在同源双标签页联调"面板分离 → #/detached/<panelId> → 跨窗口会话同步"。
// 生产构建 import.meta.env.DEV 静态替换为 false，整段（含 import）被摇树移除。
if (import.meta.env.DEV && import.meta.env.VITE_MOCK_ELECTRON === '1') {
  installMockElectron()
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppRouter />
  </StrictMode>,
)
