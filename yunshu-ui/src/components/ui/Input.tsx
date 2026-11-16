/**
 * Input —— 通用输入框（语义 Token 驱动）
 * - label：顶部标签（自动关联 htmlFor）
 * - error：错误提示（红色描边 + 错误文案）
 * - loading：等待态（右侧 spinner + 禁用输入，用于接口校验等场景）
 * - leading / trailing：输入框**内部**的前置/后置附加槽（图标、单位、清空按钮…）
 * - 颜色一律取自语义 Token，禁止硬编码
 */
import { forwardRef, useId } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

/** 尺寸档：md（默认，36px）/ sm（32px）/ xs（24px，工作台会话栏这类密集区域） */
export type InputSize = 'xs' | 'sm' | 'md'

// Omit 'size'：原生 <input size> 是「字符宽度」数字，与这里的尺寸档同名，统一由 size 档接管
export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** 顶部标签文案，不传则不显示 */
  label?: string
  /** 尺寸档，默认 md */
  size?: InputSize
  /** 错误提示文案，存在时输入框变红并显示提示 */
  error?: string
  /** 等待态：右侧显示旋转图标并禁用输入，用于异步校验等场景 */
  loading?: boolean
  /**
   * 前置附加槽：图标/单位/前缀，绝对定位在输入框**左内边距**里，正文自动让位（pl-*）。
   * 槽位默认 pointer-events-none（纯装饰，不挡输入框聚焦）—— 要在前面放可点内容才用按钮原语。
   */
  leading?: React.ReactNode
  /**
   * 后置附加槽：清空按钮/单位/快捷键提示，绝对定位在**右内边距**里。
   * 与 leading 不同，这个槽位**保留指针事件**（「清空搜索」这类按钮要能点）。
   */
  trailing?: React.ReactNode
  /** 内层 <input> 的 class —— className 仍然作用在外层容器（标签 + 输入框 + 错误文案） */
  inputClassName?: string
}

const SIZE_CLASSES: Record<InputSize, string> = {
  md: 'h-9 px-3',
  sm: 'h-8 px-2.5',
  xs: 'h-6 px-2',
}

/**
 * 附加槽的定位与正文让位量（按尺寸档给，不按内容猜）。
 * 【为什么需要】2026-11-16 会话栏搜索框只能留原生 <input>：图标是 absolute 定位在外层 relative 里的，
 * 包一层原语会把 padding 挡在 wrapper 上、图标压住正文（SessionRail 原注释）。这里把「让位」
 * 变成原语自己的事：有 leading 就给左内边距、有 trailing/loading 就给右内边距。
 */
const SLOT_CLASSES: Record<
  InputSize,
  { leadingPos: string; trailingPos: string; leadingPad: string; trailingPad: string }
> = {
  md: { leadingPos: 'left-3', trailingPos: 'right-3', leadingPad: 'pl-9', trailingPad: 'pr-9' },
  sm: { leadingPos: 'left-2.5', trailingPos: 'right-2.5', leadingPad: 'pl-8', trailingPad: 'pr-8' },
  xs: { leadingPos: 'left-2', trailingPos: 'right-2', leadingPad: 'pl-6', trailingPad: 'pr-6' },
}

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    className,
    inputClassName,
    label,
    error,
    id,
    disabled,
    loading = false,
    size = 'md',
    leading,
    trailing,
    ...props
  },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const slot = SLOT_CLASSES[size]
  const hasTrailing = loading || trailing != null

  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label htmlFor={inputId} className="mb-1.5 block text-sm font-medium text-foreground">
          {label}
        </label>
      )}
      <div className="relative">
        {leading != null && (
          <span
            className={cn(
              'pointer-events-none absolute inset-y-0 flex items-center text-muted-foreground',
              slot.leadingPos,
            )}
            data-input-leading
          >
            {leading}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          disabled={disabled || loading}
          className={cn(
            'w-full rounded-control border bg-card text-body text-foreground',
            SIZE_CLASSES[size],
            'placeholder:text-muted-foreground transition-colors',
            'focus:outline-hidden focus:ring-2 focus:ring-primary/40',
            error ? 'border-danger' : 'border-border hover:border-foreground/20',
            'disabled:cursor-not-allowed disabled:opacity-60',
            leading != null && slot.leadingPad,
            hasTrailing && slot.trailingPad,
            inputClassName,
          )}
          {...props}
        />
        {hasTrailing && (
          <span
            className={cn('absolute inset-y-0 flex items-center gap-1 text-muted-foreground', slot.trailingPos)}
            data-input-trailing
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
            {trailing}
          </span>
        )}
      </div>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
    </div>
  )
})

export default Input
