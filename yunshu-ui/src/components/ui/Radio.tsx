/**
 * Radio —— 单选框（2026-11-18）
 *
 * 【为什么补】全仓 5 处 `<input type="radio">`，形状两种：
 *   ① label 包着 radio + 文案（会话来源选择）；
 *   ② 行内独立 radio + aria-label（治理动作挑选）。
 * 两种都靠手写 className，勾选色还是浏览器默认蓝。本原语统一：label 可选、尺寸两档、
 * 勾选色走 accent-primary（与 Checkbox 同源）。
 *
 * 注意：**radio 的"组"不在这里管** —— 组的语义是 `name` + 受控 checked，
 * 各页面自己握着状态；本组件只管单个控件的长相与可达性。
 */
import { forwardRef, useId } from 'react'
import { cn } from '@/lib/cn'

export type RadioSize = 'sm' | 'md'

export interface RadioProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  /** 右侧标签（可传 JSX） */
  label?: React.ReactNode
  /** 尺寸档，默认 md（16px） */
  size?: RadioSize
}

const BOX: Record<RadioSize, string> = {
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
}

const Radio = forwardRef<HTMLInputElement, RadioProps>(function Radio(
  { className, label, size = 'md', id, disabled, ...props },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId
  const box = (
    <input
      ref={ref}
      id={inputId}
      type="radio"
      disabled={disabled}
      className={cn(
        'shrink-0 cursor-pointer accent-primary',
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

export default Radio
