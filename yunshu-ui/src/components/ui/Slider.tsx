/**
 * Slider —— 统一滑块（替代 3 种手写写法：w-full accent-cyan-500 / flex-1 accent-cyan-500 /
 * 页面 CSS 里的 .pl-slider 自定义 thumb）
 * ------------------------------------------------------------------
 * 为什么要有它：滑块此前每页各写一套，轨道高度、拇指大小、禁用态、焦点环都不一致。
 * 这里用**原生 input[type=range]**（可访问性/键盘/读屏都免费），只统一外观：
 *   · 轨道 4px、拇指 12px 圆形、主色；禁用态灰化；
 *   · 焦点走统一 :focus-visible 环（index.css）。
 */
import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

export interface SliderProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'value'> {
  value: number
  /** 数值变化回调（number，不用每次写 Number(e.target.value)） */
  onValueChange?: (next: number) => void
}

const Slider = forwardRef<HTMLInputElement, SliderProps>(function Slider(
  { className, value, onValueChange, onChange, disabled, ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type="range"
      value={value}
      disabled={disabled}
      onChange={(e) => {
        onValueChange?.(Number(e.target.value))
        onChange?.(e)
      }}
      className={cn(
        'wb-slider w-full cursor-pointer appearance-none bg-transparent',
        'disabled:cursor-not-allowed disabled:opacity-40',
        className,
      )}
      {...props}
    />
  )
})

export default Slider
