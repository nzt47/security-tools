/**
 * ThemeToggle —— 极简深浅模式切换按钮（语义 Token 驱动）
 * - 切换逻辑：<html> 增删 .dark（深浅 Token 定义在 index.css :root / .dark）
 * - 持久化：localStorage['yunshu-theme'] = 'light' | 'dark'，与 main.tsx 引导逻辑保持一致
 */
import { useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import { cn } from '@/lib/cn'

export default function ThemeToggle({ className }: { className?: string }) {
  // 初始值从 <html> 实测读取，跟随 main.tsx 的主题引导结果
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))

  function toggle() {
    const prevTheme = dark ? 'dark' : 'light'
    const nextTheme = dark ? 'light' : 'dark'
    setDark(!dark)
    document.documentElement.classList.toggle('dark', !dark)
    // 【Why】日志埋点：显式记录 localStorage 的读 / 写操作，便于排查主题持久化与多窗口状态同步问题
    const stored = localStorage.getItem('yunshu-theme')
    console.info(`[theme] 读取 localStorage['yunshu-theme']：${stored ?? '(未设置，走默认深色)'}`)
    localStorage.setItem('yunshu-theme', nextTheme)
    console.info(`[theme] 写入 localStorage['yunshu-theme']：${nextTheme}`)
    // 【Why】完整写入日志：输出写入键值对 + <html> 状态 + 含 theme 的全部键快照，核对持久化链路无残留/无双写
    const themeKeys = Object.keys(localStorage).filter((k) => k.includes('theme'))
    console.info(
      `[theme] 写入完成：{ 'yunshu-theme': '${nextTheme}', html.dark: ${!dark} }，` +
        `含 theme 键快照：${themeKeys.map((k) => `'${k}'=${localStorage.getItem(k)}`).join('、') || '(无)'}`,
    )
    console.info(`[theme] 深浅模式切换：${prevTheme} → ${nextTheme}`)
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? '切换到浅色模式' : '切换到深色模式'}
      title={dark ? '切换到浅色模式' : '切换到深色模式'}
      className={cn(
        'inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-card text-foreground',
        'transition-colors hover:bg-muted',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
        className,
      )}
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  )
}
