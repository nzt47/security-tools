/**
 * useGlobalShortcuts —— 工作台全局快捷键（2026-11-28）
 * ------------------------------------------------------------------
 * 只绑两条：Ctrl/⌘+, 开关通用设置、Ctrl/⌘+/ 定位到快捷键分区。
 * 监听挂在 window 冒泡阶段：ModalBase 的 Esc 是捕获阶段，两者互不抢；
 * 用 ref 存最新回调，避免父组件每次渲染都重挂监听。
 */
import { useEffect, useRef } from 'react'
import { isSettingsShortcut, isShortcutHelpShortcut } from './shortcuts'

export interface GlobalShortcutHandlers {
  /** Ctrl/⌘ + , —— 打开或关闭通用设置 */
  onToggleSettings: () => void
  /** Ctrl/⌘ + / —— 打开通用设置并定位到「快捷键」 */
  onOpenShortcutHelp: () => void
}

export function useGlobalShortcuts(handlers: GlobalShortcutHandlers): void {
  const ref = useRef(handlers)
  ref.current = handlers

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat) return
      if (isSettingsShortcut(event)) {
        event.preventDefault()
        ref.current.onToggleSettings()
        return
      }
      if (isShortcutHelpShortcut(event)) {
        event.preventDefault()
        ref.current.onOpenShortcutHelp()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}
