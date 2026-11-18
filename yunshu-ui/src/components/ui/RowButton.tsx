/**
 * RowButton —— 整行可点（列表行 / 折叠标题）2026-11-18
 *
 * 【为什么补】全仓有 5 处"整行可点"的原生 button：列表条目、折叠面板标题、菜单项。
 * 它们的 className 是 `flex w-full items-center ... text-left` + hover 底色 —— **视觉上不是按钮**
 * （没有边框、没有固定高度、没有按钮排版），所以既不该套 Button，也不该继续手写。
 *
 * 与 Button 的分工：
 * - Button：有明确按钮外观（描边/底色/固定高度），用于"动作"；
 * - RowButton：**行**（可含图标、标题、副标题、右侧徽章），用于"选一项/展开一层"。
 *
 * 口径：
 * - 语义仍是原生 button（键盘可达、aria-expanded 直接透传）；
 * - 选中态走 --color-primary 的淡底 + 主色文字（与 Tabs/菜单同一套）；禁用态给 opacity + cursor-not-allowed；
 * - 行高两档：sm（32px，密集菜单）/ md（40px，列表条目）。
 */
import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

export type RowButtonSize = 'sm' | 'md'

export interface RowButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** 行高，默认 md */
  size?: RowButtonSize
  /** 当前选中（列表里"就是这一项"） */
  selected?: boolean
  /** 左侧（图标/序号） */
  leading?: React.ReactNode
  /** 右侧（徽章/快捷键/箭头） */
  trailing?: React.ReactNode
}

const SIZE: Record<RowButtonSize, string> = {
  sm: 'min-h-8 px-2 py-1 text-body',
  md: 'min-h-10 px-3 py-2 text-body',
}

const RowButton = forwardRef<HTMLButtonElement, RowButtonProps>(function RowButton(
  { size = 'md', selected = false, leading, trailing, children, className, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled}
      aria-current={selected ? 'true' : undefined}
      className={cn(
        'flex w-full items-center gap-2 rounded-control text-left transition-colors',
        SIZE[size],
        selected ? 'bg-primary/15 text-primary' : 'text-foreground hover:bg-muted',
        disabled && 'cursor-not-allowed opacity-60 hover:bg-transparent',
        className,
      )}
      {...props}
    >
      {leading != null && <span className="flex shrink-0 items-center justify-center">{leading}</span>}
      {/* 正文不额外包一层 truncate：调用方可能是"标题 + 副标题 + 徽章"的复合行，
          由它自己决定哪里截断（简单行直接传文字即可）。 */}
      <span className="min-w-0 flex-1">{children}</span>
      {trailing != null && <span className="flex shrink-0 items-center gap-1">{trailing}</span>}
    </button>
  )
})

export default RowButton
