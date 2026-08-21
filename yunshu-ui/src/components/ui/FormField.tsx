/**
 * FormField —— 表单项容器（label + error 体系）
 * 与 Input / Select 的 label / error 能力对齐，供表单统一包裹使用。
 * 注意：Input/Select 自身已内置 label/error；本组件用于"一行多控件"等组合场景。
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface FormFieldProps {
  /** 标签文案，不传则不显示 */
  label?: string
  /** 错误提示文案 */
  error?: string
  /** 是否必填（label 后显示 *），仅展示标记 */
  required?: boolean
  className?: string
  children: ReactNode
}

export default function FormField({ label, error, required, className, children }: FormFieldProps) {
  return (
    <div className={cn('w-full', className)}>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-foreground">
          {label}
          {required && <span className="ml-0.5 text-danger">*</span>}
        </label>
      )}
      {children}
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
    </div>
  )
}
