/**
 * ModalBase —— 弹窗基座（受控组件）
 * ------------------------------------------------------
 * 统一弹窗外壳：createPortal + 遮罩 + Esc 关闭 + body 滚动锁定 + 标题 + footer。
 * 业务弹窗（ConfirmDialog / 各表单 Dialog）复用本组件，禁止各自手写外壳。
 * 颜色一律语义 Token：遮罩 bg-overlay/50，容器 bg-card/border-border/shadow-card。
 */
import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface ModalBaseProps {
  /** 是否显示 */
  open: boolean
  /** 关闭回调（Esc / 遮罩点击触发） */
  onClose: () => void
  /** 标题（同时也作为 aria-label；纯文本） */
  title?: string
  /**
   * 【2026-11-18 补齐】自定义标题内容（可带图标/徽标）。给了它就渲染这一段，
   * 不再渲染 title 文本（title 仍作为 aria-label）。
   */
  header?: ReactNode
  /** 底部操作区（如确认/取消按钮组，可选） */
  footer?: ReactNode
  /** 内容区宽度约束，默认 max-w-md */
  width?: string
  /** 点击遮罩是否关闭，默认 true */
  closeOnMask?: boolean
  /**
   * Esc 是否关闭，默认 true。
   * 【为什么需要】阻塞式确认（如对话内工具审批）不允许用 Esc"走开"，
   * 但它仍然要 portal / 遮罩 / 滚动锁定这些外壳能力。
   */
  closeOnEsc?: boolean
  /**
   * 形态：dialog（默认，居中）/ drawer（右侧抽屉，满高、左描边、宽度由 width 控制）。
   * 【为什么需要】技能详情这类"抽屉"此前只能自己写外壳（右侧 + 全高 + 自带 tab 头）。
   */
  variant?: 'dialog' | 'drawer'
  /** 透传给对话框容器的 data-testid（测试/探针用） */
  testId?: string
  /**
   * 无障碍名（role=dialog 的 aria-label），默认取 title。
   * 【为什么需要】抽屉/自定义头部这类弹窗，标题区由调用方自己画（不用 ModalBase 的 header），
   * 但仍要有一个稳定的无障碍名（测试与读屏都按它找）。
   */
  ariaLabel?: string
  /**
   * 【2026-11-18 补齐】容器附加类：长内容需要 `max-h-[85vh] overflow-y-auto`、
   * 详情弹窗需要更宽的 `max-w-3xl` 之外的排版 —— 此前无法表达，于是页面宁可自己写外壳。
   */
  className?: string
  /** 内容区附加类（如 gap / padding 调整） */
  bodyClassName?: string
  /**
   * 长内容可滚动：容器限高 85vh、正文区自己滚（标题/footer 不滚）。
   * 与 `className` 的区别是它只做这一件事，不用调用方记 max-h 的数值。
   */
  scrollable?: boolean
  /**
   * 右上角 × 关闭按钮：默认 **关闭**（既有 6 处调用点无按钮，改默认会动它们的观感）。
   * 新迁移的弹窗基本都要它。
   */
  closeButton?: boolean
  children: ReactNode
}

export default function ModalBase({
  open,
  onClose,
  title,
  header,
  footer,
  width = 'max-w-md',
  closeOnMask = true,
  closeOnEsc = true,
  variant = 'dialog',
  testId,
  ariaLabel,
  className,
  bodyClassName,
  scrollable = false,
  closeButton = false,
  children,
}: ModalBaseProps) {
  // 焦点管理：打开时记录触发元素，关闭后还原，避免焦点丢失在 body
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    restoreFocusRef.current = document.activeElement as HTMLElement | null
    const onKeydown = (e: KeyboardEvent) => {
      if (closeOnEsc && e.key === 'Escape') onClose()
    }
    // 【2026-11-18】挂**捕获**阶段：工作台面板内部的 Esc 处理会 stopPropagation，
    // 冒泡监听拿不到事件（§11.137 坑 5 的同一条）。弹窗打开时它必须先关。
    window.addEventListener('keydown', onKeydown, true)
    // body 滚动锁定：弹窗打开期间阻止背景滚动
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeydown, true)
      document.body.style.overflow = prevOverflow
      restoreFocusRef.current?.focus()
    }
  }, [open, onClose, closeOnEsc])

  if (!open) return null

  return createPortal(
    <div className={variant === 'drawer'
      ? 'fixed inset-0 z-50 flex justify-end'
      : 'fixed inset-0 z-50 flex items-center justify-center p-4'}>
      {/* 遮罩：点击关闭（closeOnMask=false 时仅展示，不响应） */}
      <div
        className="absolute inset-0 bg-overlay/50"
        onClick={closeOnMask ? onClose : undefined}
        aria-hidden
      />
      <div
        className={cn(
          'relative flex w-full flex-col border-border bg-card text-card-foreground shadow-card',
          variant === 'drawer'
            ? 'h-full rounded-none border-l max-w-2xl'
            : 'rounded-lg border',
          scrollable && (variant === 'drawer' ? '' : 'max-h-[85vh]'),
          variant === 'dialog' && width,
          className,
        )}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel ?? title}
        data-testid={testId}
      >
        {(title || header || closeButton) && (
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
            {header != null ? (
              <div className="min-w-0 truncate text-base font-semibold text-foreground">{header}</div>
            ) : title ? (
              <h3 className="min-w-0 truncate text-body font-semibold text-foreground">{title}</h3>
            ) : (
              <span />
            )}
            {closeButton && (
              <button
                type="button"
                aria-label="关闭"
                onClick={onClose}
                className="shrink-0 rounded-control p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            )}
          </div>
        )}
        <div className={cn('px-5 py-4', scrollable && 'min-h-0 overflow-y-auto', bodyClassName)}>{children}</div>
        {footer && (
          <div className="flex justify-end gap-3 border-t border-border px-5 py-4">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  )
}