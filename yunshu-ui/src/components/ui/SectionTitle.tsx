/**
 * SectionTitle —— 区块标题（统一字号/字重/间距与前置色点）
 * ------------------------------------------------------------------
 * 原来每页各写一套：.pl-category-title / .wb-*title / <h2 className="text-[13px]"> …
 * 字号散装 17 个取值（含 10.5/11.5 半像素）。这里统一成 text-title（16px）/semibold，
 * 前置 dot 用主色，右侧 actions 槽放计数或按钮。
 */
import { cn } from '@/lib/cn'

export type SectionTitleLevel = 1 | 2 | 3

export interface SectionTitleProps {
  children: React.ReactNode
  level?: SectionTitleLevel
  /** 是否显示前置主色圆点（区块型标题用 true，表单小标题用 false） */
  dot?: boolean
  /** 右侧槽：计数、开关、按钮 */
  actions?: React.ReactNode
  className?: string
}

const LEVEL_CLASS: Record<SectionTitleLevel, string> = {
  1: 'text-h1',
  2: 'text-title',
  3: 'text-body-lg',
}

export default function SectionTitle({ children, level = 2, dot = true, actions, className }: SectionTitleProps) {
  const Tag = (level === 1 ? 'h1' : level === 2 ? 'h2' : 'h3') as 'h1' | 'h2' | 'h3'
  return (
    <div className={cn('flex items-center justify-between gap-3', className)}>
      <Tag className={cn('flex min-w-0 items-center gap-2 font-semibold text-foreground', LEVEL_CLASS[level])}>
        {dot && <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />}
        <span className="truncate">{children}</span>
      </Tag>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  )
}
