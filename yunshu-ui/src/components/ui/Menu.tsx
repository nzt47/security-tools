/**
 * Menu —— 下拉菜单的**行为**（触发器 + 浮层），不规定菜单长什么样
 * ------------------------------------------------------------------
 * 【为什么补这一档】2026-11-18 封装审计：全仓有 5 处各自手写「触发器 + 浮层面板 +
 * 外点关闭 + Esc 关闭」，每处一份 `useEffect`（ChatModeMenu / PermissionModeMenu /
 * PanelMenu / ContextManagerBar / SessionRail）。它们的**行为**完全一样，只是长得不一样。
 *
 * 设计：把行为拆成两半，外观留给调用方 ——
 *   · `useMenu()`：open 状态 + 锚点 ref + toggle/close；
 *   · `<MenuPanel anchorRef onClose>`：portal 到 body、按锚点定位（fixed）、
 *     外点关闭、**Esc 挂捕获阶段**（工作台面板内部的 Esc 处理会 stopPropagation，冒泡拿不到）、
 *     滚动/缩放时自动收起。
 *
 * 浮层一律 portal 到 body：留在 mosaic 子树里会被别的层叠上下文压住（§11.137 坑 4）。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/cn'

export interface MenuState<T extends HTMLElement = HTMLElement> {
  open: boolean
  setOpen: (v: boolean) => void
  toggle: () => void
  close: () => void
  /** 触发器元素的 ref（MenuPanel 靠它定位）；泛型让 Button/原生 button 的 ref 都能对上 */
  anchorRef: RefObject<T | null>
}

export function useMenu<T extends HTMLElement = HTMLElement>(): MenuState<T> {
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<T | null>(null)
  const close = useCallback(() => setOpen(false), [])
  const toggle = useCallback(() => setOpen((v) => !v), [])
  return { open, setOpen, toggle, close, anchorRef }
}

export interface MenuPanelProps {
  /** 触发器 ref（useMenu().anchorRef） */
  anchorRef: RefObject<HTMLElement | null>
  /** 关闭回调（外点 / Esc / 滚动 / 窗口缩放） */
  onClose: () => void
  /** 对齐方式：右对齐（默认，菜单挂触发器右下）/ 左对齐 */
  align?: 'left' | 'right'
  /** 与触发器的垂直间距（px），默认 6 */
  offset?: number
  /** 面板宽度（px），默认 320 */
  width?: number
  /** 无障碍名（role=menu 的 aria-label） */
  label?: string
  className?: string
  children: ReactNode
}

export function MenuPanel({
  anchorRef,
  onClose,
  align = 'right',
  offset = 6,
  width = 320,
  label,
  className,
  children,
}: MenuPanelProps) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // 定位：按锚点算 fixed 坐标，并夹在视口内（右边/下边不溢出）
  useLayoutEffect(() => {
    const place = () => {
      const a = anchorRef.current?.getBoundingClientRect()
      if (!a) return
      const margin = 8
      let left = align === 'right' ? a.right - width : a.left
      left = Math.min(Math.max(margin, left), Math.max(margin, window.innerWidth - width - margin))
      const top = Math.min(a.bottom + offset, Math.max(margin, window.innerHeight - 120))
      setPos({ top, left })
    }
    place()
    window.addEventListener('resize', onClose)
    return () => window.removeEventListener('resize', onClose)
  }, [anchorRef, align, width, offset, onClose])

  // 外点关闭 + Esc（捕获阶段）+ 滚动关闭
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (panelRef.current?.contains(t)) return
      if (anchorRef.current?.contains(t)) return // 点触发器交给它自己的 toggle
      onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const onScroll = () => onClose()
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [onClose, anchorRef])

  if (!pos) return null
  return createPortal(
    <div
      ref={panelRef}
      role="menu"
      aria-label={label}
      style={{ position: 'fixed', top: pos.top, left: pos.left, width }}
      className={cn(
        'z-[80] rounded-xl border border-border bg-card p-2 text-body text-card-foreground shadow-card',
        className,
      )}
    >
      {children}
    </div>,
    document.body,
  )
}