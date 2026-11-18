/**
 * Textarea —— 通用多行输入（语义 Token 驱动）
 * - 只渲染控件本身：**不**带 label / 错误文案（label 用 FormField 或页面既有 .pl-field 包一层）。
 *   这样它既能进表单栅格，也能替换 .pl-syspart-text 这类"文本域本身就是布局元素"的场景
 *   （Input 的 wrapper div 在这些位置会多出一层块级盒子）。
 * - 尺寸档：md（默认，min-h 72）/ sm（一/两行的紧凑用法）
 * - 颜色一律取自语义 Token，禁止硬编码
 */
import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

/** 尺寸档：md（默认，多行编辑）/ sm（一两行的短文本） */
export type TextareaSize = 'sm' | 'md'

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** 尺寸档，默认 md */
  size?: TextareaSize
  /**
   * 形态：default / bare（外观完全交给调用方的 CSS 类，见 Input.variant 的同一条理由）。
   * 常用于 .kb-form-textarea / .pl-prompt-out / .wb-input / .wb-editor-input 这类"CSS 已定义外观"的位置。
   */
  variant?: 'default' | 'bare'
  /** 错误态：红色描边（文案由调用方渲染） */
  error?: boolean
}

const SIZE_CLASSES: Record<TextareaSize, string> = {
  md: 'min-h-[72px] px-3 py-2',
  sm: 'px-2.5 py-1.5',
}

const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, error = false, size = 'md', variant = 'default', ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      aria-invalid={error ? true : undefined}
      className={cn(
        'w-full',
        variant === 'default'
          ? [
              'resize-y rounded-control border bg-card text-body text-foreground',
              'placeholder:text-muted-foreground transition-colors',
              'focus:outline-hidden focus:ring-2 focus:ring-primary/40',
              error ? 'border-danger' : 'border-border hover:border-foreground/20',
              'disabled:cursor-not-allowed disabled:opacity-60',
              SIZE_CLASSES[size],
            ]
          : 'border-0 bg-transparent p-0 text-inherit focus:outline-hidden',
        className,
      )}
      {...props}
    />
  )
})

export default Textarea