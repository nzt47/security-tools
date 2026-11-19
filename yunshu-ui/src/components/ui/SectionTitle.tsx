/**
 * SectionTitle —— 区块标题（全仓唯一的区块标题原语）
 * ------------------------------------------------------------------
 * 原来每页各写一套：.pl-category-title / .wb-*title / <h2 className="text-ui"> …
 * 字号散装 17 个取值（含 10.5/11.5 半像素）。
 *
 * 【2026-11-19 对齐 heading 阶梯 B + 收掉「第二套」】Owner 报「TAB 的小标题字号不统一」：
 * 同一层的区块标题当时有 16（本组件 level=2）/ 14（手写 h2 text-ui）/ 13 / 11.5 多种。
 * 现在按阶梯 B 固定：h1 = text-title（16）/ h2 = text-ui（14）/ h3 = text-body（12）；
 * 说明行恒 text-caption（11）。props 同时接受 children 与 title（子模块从 PageHeader
 * 迁过来时不用改文案），description / icon / actions 与页头同形。
 */
import { cn } from '@/lib/cn'

export type SectionTitleLevel = 1 | 2 | 3

export interface SectionTitleProps {
  children?: React.ReactNode
  /** 文案（与 children 二选一；子模块从 PageHeader 迁移时用这个） */
  title?: React.ReactNode
  level?: SectionTitleLevel
  /** 是否显示前置主色圆点（区块型标题用 true，表单小标题用 false） */
  dot?: boolean
  /** 标题前的图标（与 dot 可同时存在；图标不替代 dot） */
  icon?: React.ReactNode
  /** 说明行：恒 text-caption（11px） */
  description?: React.ReactNode
  /** 右侧槽：计数、开关、按钮 */
  actions?: React.ReactNode
  className?: string
}

const LEVEL_CLASS: Record<SectionTitleLevel, string> = {
  1: 'text-title',
  2: 'text-ui',
  3: 'text-body',
}

export default function SectionTitle({
  children, title, level = 2, dot = true, icon, description, actions, className,
}: SectionTitleProps) {
  const Tag = (level === 1 ? 'h1' : level === 2 ? 'h2' : 'h3') as 'h1' | 'h2' | 'h3'
  return (
    <div className={cn('flex items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        <Tag className={cn('flex min-w-0 items-center gap-2 font-semibold text-foreground', LEVEL_CLASS[level])}>
          {dot && <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />}
          {icon}
          <span className="truncate">{title ?? children}</span>
        </Tag>
        {description ? <p className="mt-1 text-caption text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  )
}
