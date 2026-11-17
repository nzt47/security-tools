/**
 * Checkbox —— 原生复选框 + 语义 token（2026-11-18）
 *
 * 【为什么补这一档】全仓曾有 46 个原生 `<input type="checkbox">`，每处自己拼 label 与颜色，
 * 而 `components/ui` 里只有 Switch —— 于是「一次性确认」类复选框（我确认覆盖摘要 / 我确认清空）
 * 只能手写。**开关与复选框是两种语义**（Switch = 状态列，Checkbox = 一次性确认，见口径 Owner 口径），
 * 所以两者都要有原语，不能拿 Switch 顶。
 *
 * - label：可传 JSX（现有确认框常带 <b> 强调）
 * - size：sm（14px，密集表单）/ md（16px，默认）
 * - 勾选色走 accent-color: --color-primary（不用手绘方块 + 对勾图标）
 */
import { forwardRef, useId } from 'react'
import { cn } from '@/lib/cn'

export type CheckboxSize = 'sm' | 'md'

export interface CheckboxProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  /** 右侧标签（可传 JSX） */
  label?: React.ReactNode
  /** 尺寸档，默认 md（16px） */
  size?: CheckboxSize
}

const BOX: Record<CheckboxSize, string> = {
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
}

const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { className, label, size = 'md', id, disabled, ...props },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const box = (
    <input
      ref={ref}
      id={inputId}
      type="checkbox"
      disabled={disabled}
      className={cn(
        'shrink-0 cursor-pointer rounded-sm border-border accent-primary',
        'disabled:cursor-not-allowed disabled:opacity-60',
        BOX[size],
        label == null ? className : undefined,
      )}
      {...props}
    />
  )
  if (label == null) return box
  return (
    <label
      htmlFor={inputId}
      className={cn(
        'inline-flex cursor-pointer items-center gap-1.5 text-caption text-foreground',
        disabled && 'cursor-not-allowed opacity-60',
        className,
      )}
    >
      {box}
      <span>{label}</span>
    </label>
  )
})

export default Checkbox
