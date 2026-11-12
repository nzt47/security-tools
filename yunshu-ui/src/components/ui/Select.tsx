/**
 * Select —— 通用下拉选择（原生 select + 语义 Token）
 * 禁引重型 UI 库，用原生 select 满足业务下拉需求。
 */
import { forwardRef, useId } from 'react'
import { cn } from '@/lib/cn'

export interface SelectOption {
  label: string
  value: string
}

export interface SelectProps
  extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange'> {
  /** 选项列表 */
  options: SelectOption[]
  /** 受控值；undefined 时显示 placeholder 且值为空串 */
  value?: string
  onChange?: (value: string) => void
  /** 顶部标签文案，不传则不显示 */
  label?: string
  /** 未选择时的提示项文案，默认「请选择」 */
  placeholder?: string
  /**
   * 提示项是否禁用（默认 true = 纯占位，选了就回不去）。
   * 【为什么需要这个开关】"全部类型"这类**空值本身就是有效筛选值**的下拉，占位项必须可选，
   * 否则用户选过一次类型就再也回不到"全部"，只能去点重置（2026-11-13 消息中心实测）。
   */
  placeholderOptionDisabled?: boolean
  /** 错误提示文案，存在时红色描边并显示提示 */
  error?: string
}

const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, options, value, onChange, label, placeholder = '请选择',
    placeholderOptionDisabled = true, error, id, ...props },
  ref,
) {
  const autoId = useId()
  const selectId = id ?? autoId

  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label htmlFor={selectId} className="mb-1.5 block text-sm font-medium text-foreground">
          {label}
        </label>
      )}
      <select
        ref={ref}
        id={selectId}
        aria-invalid={error ? true : undefined}
        value={value ?? ''}
        onChange={(e) => onChange?.(e.target.value)}
        className={cn(
          'h-9 w-full rounded-md border bg-card px-3 text-sm text-foreground',
          'transition-colors focus:outline-hidden focus:ring-2 focus:ring-primary/40',
          error ? 'border-danger' : 'border-border hover:border-foreground/20',
        )}
        {...props}
      >
        <option value="" disabled={placeholderOptionDisabled}>
          {placeholder}
        </option>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
    </div>
  )
})

export default Select
