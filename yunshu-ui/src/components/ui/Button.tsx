/**
 * Button —— 通用按钮（语义 Token 驱动）
 * - variant：primary（主操作）/ default（次级）/ danger（危险）/ ghost（幽灵）
 * - size：sm / md
 * - loading：加载态（禁用 + 旋转图标，防重复提交）
 * - 颜色一律取自语义 Token（tailwind.config.js / index.css），禁止硬编码
 */
import { forwardRef } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export type ButtonVariant = 'primary' | 'default' | 'danger' | 'ghost'
/**
 * icon：正方形只放图标（32×32），用于工具栏/卡片右上角
 * xs  ：行内/工具条上的紧凑控件（24px 高，text-micro）—— 用于右侧面板、会话行等密集区域；
 *       这些位置原来手写 `rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px]`。
 */
export type ButtonSize = 'xs' | 'sm' | 'md' | 'icon'

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** 视觉类型，默认 default */
  variant?: ButtonVariant
  /** 尺寸，默认 md */
  size?: ButtonSize
  /** 加载态：禁用按钮并显示 spinner */
  loading?: boolean
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
  default: 'border border-border bg-card text-foreground hover:bg-muted',
  danger: 'bg-danger text-danger-foreground hover:bg-danger/90',
  ghost: 'text-foreground hover:bg-muted',
}

const SIZE_CLASSES: Record<ButtonSize, string> = {
  xs: 'h-6 rounded-control px-2 text-micro',
  sm: 'h-8 rounded-control px-3 text-body',
  md: 'h-9 rounded-control px-4 text-ui',
  icon: 'h-8 w-8 rounded-control p-0',
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'default', size = 'md', loading = false, disabled, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled || loading}
      className={cn(
        // whitespace-nowrap：按钮标签不允许折行。固定高度（h-6/h-8/h-9）下折行会溢出到相邻元素上，
        // 而 flex 项的 min-width:auto = min-content（整段标签），所以 nowrap 同时也让按钮不被挤窄（2026-11-16 实测：
        // hub 的「前往组装台」在 1280 下被压到 61px 宽、标签折成两行溢出 43px > 32px）。
        'inline-flex items-center justify-center gap-1.5 whitespace-nowrap font-medium transition-colors',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/40',
        'disabled:cursor-not-allowed disabled:opacity-60',
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  )
})

export default Button
