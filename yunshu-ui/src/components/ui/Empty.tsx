/**
 * Empty —— 空态占位（语义 Token 驱动）
 * 表格空数据 / 列表无结果等场景统一使用。
 */
import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface EmptyProps {
  /** 提示文案，默认「暂无数据」 */
  description?: string
  /** 自定义图标/插图，缺省用 Inbox 空箱 */
  icon?: ReactNode
  /** 额外操作区（如"重新加载"按钮） */
  children?: ReactNode
  className?: string
}

export default function Empty({
  description = '暂无数据',
  icon,
  children,
  className,
}: EmptyProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 py-10', className)}>
      <div className="text-muted-foreground/50">{icon ?? <Inbox className="h-10 w-10" aria-hidden />}</div>
      <p className="text-sm text-muted-foreground">{description}</p>
      {children}
    </div>
  )
}
