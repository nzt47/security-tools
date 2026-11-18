/**
 * Notice —— 提示条（成功 / 失败 / 警告 / 信息）
 * ------------------------------------------------------------------
 * 【为什么补这一档】2026-11-18 封装审计：全仓有 **87 处手写提示条**散在 53 个文件里，
 * 每处自己拼 `border-red-900/60 bg-red-950/30 text-red-300` 这类字面值 ——
 * 同一种"操作结果"出现 4 种红、3 种绿，而且与 index.css 的语义 token（--color-danger /
 * --color-success / --color-warning / --color-primary）完全脱钩：改一次主题色，这些地方不会跟着变。
 *
 * 语义：tone 决定颜色与默认图标，size 决定密度；文案由调用方给（children）。
 * 颜色一律走 token，禁止硬编码色值。
 */
import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info } from 'lucide-react'
import { cn } from '@/lib/cn'

/** 语义：成功 / 失败（危险）/ 警告 / 信息 */
export type NoticeTone = 'success' | 'danger' | 'warning' | 'info'

/** 密度：sm（一行短提示）/ md（默认，成段说明） */
export type NoticeSize = 'sm' | 'md'

/** Omit 'title'：原生 title 是 tooltip 字符串，这里要的是「加粗标题行」，语义不同 */
export interface NoticeProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  /** 语义档，默认 info */
  tone?: NoticeTone
  /** 密度档，默认 md */
  size?: NoticeSize
  /**
   * 前置图标：不传按 tone 给默认图标；传 `null` 表示**不要图标**
   * （短提示、行内提示里图标反而占地）。
   */
  icon?: ReactNode
  /** 可选标题（加粗一行，正文在其下） */
  title?: ReactNode
}

const TONE_CLASSES: Record<NoticeTone, string> = {
  success: 'border-success/40 bg-success/10 text-success',
  danger: 'border-danger/40 bg-danger/10 text-danger',
  warning: 'border-warning/40 bg-warning/10 text-warning',
  info: 'border-primary/40 bg-primary/10 text-primary',
}

const SIZE_CLASSES: Record<NoticeSize, string> = {
  sm: 'gap-1.5 rounded-sm px-2 py-1 text-caption',
  md: 'gap-2 rounded-lg px-3 py-2 text-body',
}

const DEFAULT_ICON: Record<NoticeTone, ReactNode> = {
  success: <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden />,
  danger: <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />,
  warning: <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />,
  info: <Info className="h-3.5 w-3.5 shrink-0" aria-hidden />,
}

export default function Notice({
  tone = 'info',
  size = 'md',
  icon,
  title,
  className,
  children,
  ...props
}: NoticeProps) {
  const glyph = icon === undefined ? DEFAULT_ICON[tone] : icon
  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className={cn('flex items-start border font-normal', TONE_CLASSES[tone], SIZE_CLASSES[size], className)}
      {...props}
    >
      {glyph != null && <span className="mt-px shrink-0">{glyph}</span>}
      <div className="min-w-0 flex-1">
        {title != null && <div className="font-medium">{title}</div>}
        {children != null && <div className={title != null ? 'mt-0.5 opacity-90' : undefined}>{children}</div>}
      </div>
    </div>
  )
}
