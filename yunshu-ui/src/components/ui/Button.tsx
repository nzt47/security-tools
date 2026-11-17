/**
 * Button —— 通用按钮（语义 Token 驱动）
 * - variant：primary（主操作）/ default（次级）/ accent（描边强调）/ danger（实心危险）
 *            / danger-outline（描边危险）/ ghost（幽灵）
 * - size：xs / sm / md / icon
 * - loading：加载态（禁用 + 旋转图标，防重复提交）
 * - 颜色一律取自语义 Token（tailwind.config.js / index.css），禁止硬编码
 */
import { forwardRef } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

/**
 * accent / danger-outline 是 2026-11-17 补的两个**描边**变体：
 * 表格行内与密集面板里的"信息/进阶动作"（原 cyan 描边字）与"删除"（原 rose 描边字）都不该做成实心块
 * —— 一行 5 个按钮时实心会把正文的视觉权重抢走（lib/tableActions.ts 的老口径）。
 * 此前这两档只能靠各页手拼 `border-cyan-700/70 text-cyan-300` 这类第五、第六种强调色，
 * 现在收口到语义 token：accent = 主色描边，danger-outline = 危险色描边。
 */
export type ButtonVariant =
  | 'primary'
  | 'default'
  | 'accent'
  | 'success-outline'
  | 'danger'
  | 'danger-outline'
  | 'ghost'
/**
 * icon：正方形只放图标（32×32），用于工具栏/卡片右上角
 * xs  ：行内/工具条上的紧凑控件（24px 高，text-micro）—— 用于右侧面板、会话行等密集区域；
 *       这些位置原来手写 `rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px]`。
 */
/**
 * icon / icon-sm / icon-xs 是**三档正方形**：32 / 24 / 20。
 * 【为什么要有三档】只有 32 一档时，面板标题行里的 ×/刷新图标只能用
 * `size="icon" className="h-5 w-5"` 这种"外面套原语、里面再覆盖尺寸"的写法 —— 那等于原语没封装住：
 * 2026-11-18 全仓数到 18 处这种覆盖。现在三档都在原语里，调用方只挑档、不再写 h-/w-。
 */
export type ButtonSize = 'xs' | 'sm' | 'md' | 'icon' | 'icon-sm' | 'icon-xs'

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
  accent: 'border border-primary/50 text-primary hover:bg-primary/10',
  // 「正向但不必抢视线」的动作（登记 / 取消归档 / 通过）：描边绿。原先是手写 emerald 描边字，
  // 与 accent（主色）/ danger-outline（危险）拼成一套"语义描边"，颜色只来自 --color-success。
  'success-outline': 'border border-success/50 text-success hover:bg-success/10',
  danger: 'bg-danger text-danger-foreground hover:bg-danger/90',
  'danger-outline': 'border border-danger/50 text-danger hover:bg-danger/10',
  ghost: 'text-foreground hover:bg-muted',
}

const SIZE_CLASSES: Record<ButtonSize, string> = {
  xs: 'h-6 rounded-control px-2 text-micro',
  sm: 'h-8 rounded-control px-3 text-body',
  md: 'h-9 rounded-control px-4 text-ui',
  icon: 'h-8 w-8 rounded-control p-0',
  'icon-sm': 'h-6 w-6 rounded-control p-0',
  'icon-xs': 'h-5 w-5 rounded-control p-0',
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
