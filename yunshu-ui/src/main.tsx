import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import AppRouter from './router'
import Toaster from './components/Toaster'
import { startCrossWindowSync } from './electron/sync'
import { installMockElectron } from './electron/mockElectron'
import './index.css'

// 主题引导：桌面应用默认深色；localStorage['yunshu-theme'] 为 'light' 时切浅色。
// 深浅 Token 定义在 index.css（:root / .dark），切换只需在 <html> 增删 .dark。
if (localStorage.getItem('yunshu-theme') !== 'light') {
  document.documentElement.classList.add('dark')
}

// 主题跨窗口同步监听：storage 事件仅在【其他窗口/标签页】写入 localStorage 时触发
// （本窗口自身写入不触发），用于桌面多窗口场景下主题状态一致性排查。
window.addEventListener('storage', (e) => {
  if (e.key !== 'yunshu-theme') return
  // 与引导逻辑一致：'light' 之外一律视为深色
  document.documentElement.classList.toggle('dark', e.newValue !== 'light')
  console.info(
    `[theme] storage 事件（跨窗口）：key=${e.key}，` +
      `oldValue=${e.oldValue ?? '(未设置)'} → newValue=${e.newValue ?? '(已清除)'}，` +
      `本窗口已同步 html.dark=${e.newValue !== 'light'}`,
  )
})

// Web 联调模式：VITE_MOCK_ELECTRON=1 时注入 mock Electron API（双标签页模拟多窗口）
if (import.meta.env.VITE_MOCK_ELECTRON === '1') {
  installMockElectron()
}

/**
 * 入口：HashRouter 路由分发 + 跨窗口状态同步
 * ------------------------------------------------
 * 路由配置见 src/router/index.tsx（含登录守卫 / MainLayout / 独立窗口路由）。
 * 每个渲染进程（主窗口 / 独立窗口）启动时调用 startCrossWindowSync()，
 * 经主进程事件总线同步 messages/thinking（Web 环境自动降级为空操作）。
 */
function Root() {
  // 跨窗口状态同步（全进程级，仅一次）
  useEffect(() => startCrossWindowSync(), [])

  return <AppRouter />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
    {/* 全局 Toast（request.ts 拦截器错误提示等，全局唯一挂载点） */}
    <Toaster />
  </StrictMode>,
)
