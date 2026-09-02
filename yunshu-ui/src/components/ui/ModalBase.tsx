/**
 * ModalBase —— 弹窗基座（受控组件）
 * ------------------------------------------------------
 * 统一弹窗外壳：createPortal + 遮罩 + Esc 关闭 + body 滚动锁定 + 标题 + footer。
 * 业务弹窗（ConfirmDialog / 各表单 Dialog）复用本组件，禁止各自手写外壳。
 * 颜色一律语义 Token：遮罩 bg-overlay/50，容器 bg-card/border-border/shadow-card。
 */
import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/cn'

export interface ModalBaseProps {
  /** 是否显示 */
  open: boolean
  /** 关闭回调（Esc / 遮罩点击触发） */
  onClose: () => void
  /** 标题（显示于弹窗顶部，可选） */
  title?: string
  /** 底部操作区（如确认/取消按钮组，可选） */
  footer?: ReactNode
  /** 内容区宽度约束，默认 max-w-md */
  width?: string
  /** 点击遮罩是否关闭，默认 true */
  closeOnMask?: boolean
  children: ReactNode
}

export default function ModalBase({
  open,
  onClose,
  title,
  footer,
  width = 'max-w-md',
  closeOnMask = true,
  children,
}: ModalBaseProps) {
  // 焦点管理：打开时记录触发元素，关闭后还原，避免焦点丢失在 body
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    restoreFocusRef.current = document.activeElement as HTMLElement | null
    const onKeydown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeydown)
    // body 滚动锁定：弹窗打开期间阻止背景滚动
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeydown)
      document.body.style.overflow = prevOverflow
      restoreFocusRef.current?.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* 遮罩：点击关闭（closeOnMask=false 时仅展示，不响应） */}
      <div
        className="absolute inset-0 bg-overlay/50"
        onClick={closeOnMask ? onClose : undefined}
        aria-hidden
      />
      <div
        className={cn(
          'relative w-full rounded-lg border border-border bg-card text-card-foreground shadow-card',
          width,
        )}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {title && (
          <div className="border-b border-border px-5 py-4">
            <h3 className="text-base font-semibold text-foreground">{title}</h3>
          </div>
        )}
        <div className="px-5 py-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-3 border-t border-border px-5 py-4">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  )
}
