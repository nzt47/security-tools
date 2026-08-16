import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import WorkbenchApp from './WorkbenchApp'
import { DetachedChatApp } from './DetachedChatApp'
import PromptLab from './pages/PromptLab'
import { startCrossWindowSync } from './electron/sync'
import { installMockElectron } from './electron/mockElectron'
import './index.css'

// Web 联调模式：VITE_MOCK_ELECTRON=1 时注入 mock Electron API（双标签页模拟多窗口）
if (import.meta.env.VITE_MOCK_ELECTRON === '1') {
  installMockElectron()
}

/**
 * 入口：hash 路由分发 + 跨窗口状态同步
 * ------------------------------------------------
 *  - #/detached/<panelId>  → 独立窗口视图（Electron 面板分离后加载）
 *  - 其它                → 主工作台
 * 每个渲染进程（主窗口 / 独立窗口）启动时调用 startCrossWindowSync()，
 * 经主进程事件总线同步 messages/thinking（Web 环境自动降级为空操作）。
 */
function Root() {
  const [hash, setHash] = useState(window.location.hash)

  useEffect(() => {
    const onChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  // 跨窗口状态同步（全进程级，仅一次）
  useEffect(() => startCrossWindowSync(), [])

  // 提示词影响因素实验室：独立路由页面（不进 Mosaic 布局）
  if (hash.startsWith('#/prompt-lab')) {
    return <PromptLab />;
  }

  if (hash.startsWith('#/detached/')) {
    const panelId = hash.slice('#/detached/'.length)
    // 白名单校验：仅允许已知面板，非法值回退主工作台
    if (panelId === 'chat' || panelId === 'think' || panelId === 'nav' || panelId === 'code') {
      return <DetachedChatApp panelId={panelId} />
    }
  }
  return <WorkbenchApp />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
