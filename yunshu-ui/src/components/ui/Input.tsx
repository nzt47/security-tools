/**
 * Input —— 通用输入框（语义 Token 驱动）
 * - label：顶部标签（自动关联 htmlFor）
 * - error：错误提示（红色描边 + 错误文案）
 * - loading：等待态（右侧 spinner + 禁用输入，用于接口校验等场景）
 * - 颜色一律取自语义 Token，禁止硬编码
 */
import { forwardRef, useId } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** 顶部标签文案，不传则不显示 */
  label?: string
  /** 错误提示文案，存在时输入框变红并显示提示 */
  error?: string
  /** 等待态：右侧显示旋转图标并禁用输入，用于异步校验等场景 */
  loading?: boolean
}

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, label, error, id, disabled, loading = false, ...props },
  ref,
) {
  const autoId = useId()
  const inputId = id ?? autoId

  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label htmlFor={inputId} className="mb-1.5 block text-sm font-medium text-foreground">
          {label}
        </label>
      )}
      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          aria-invalid={error ? true : undefined}
          disabled={disabled || loading}
          className={cn(
            'h-9 w-full rounded-md border bg-card px-3 text-sm text-foreground',
            'placeholder:text-muted-foreground transition-colors',
            'focus:outline-none focus:ring-2 focus:ring-primary/40',
            error ? 'border-danger' : 'border-border hover:border-foreground/20',
            'disabled:cursor-not-allowed disabled:opacity-60',
            loading && 'pr-9',
          )}
          {...props}
        />
        {loading && (
          <Loader2
            className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground"
            aria-hidden
          />
        )}
      </div>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
    </div>
  )
})

export default Input
