import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import PromptLab from './pages/PromptLab'
import './index.css'

/**
 * 入口（2026-08-30 构建修复）：
 * - 默认渲染 legacy 主界面（App.tsx，与 static/ 现有构建产物一致）。
 * - `#/prompt-lab` → 提示词影响因素实验室（独立页面，bb2e9915 引入）。
 * - 注：workbench/Electron 版入口（WorkbenchApp / DetachedChatApp /
 *   electron/* / lib/mosaic 等）来自未合入 master 的分支（4034e804），
 *   其源码在 master 缺失导致 2026-08-16 起构建失败；本修复回退到 legacy
 *   入口并移除相应孤儿源码，workbench 功能保留在 feature 分支。
 */
function Root() {
  const hash = window.location.hash

  if (hash.startsWith('#/prompt-lab')) {
    return <PromptLab />
  }

  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
