/**
 * Badge —— 状态/计数徽章（2026-11-18，从 pages/hub 提到全局原语）
 *
 * 【为什么提到全局】全仓有 35 处手写 `rounded-full + text-micro/xs + 底色` 的徽章：
 * 一半在系统页（已用语义 token），一半在 hub/workbench（仍在写 bg-slate-800 / bg-rose-500 / bg-cyan-500
 * 这类 tailwind 色名）。同一屏上"待处理"可能是 rose、也可能是 amber，取决于哪一页写的。
 *
 * 口径：
 * - **tone 是语义**（neutral / primary / success / warning / danger），颜色全部来自 index.css 的 token；
 * - 尺寸两档（sm=10px / md=12px），形状只有一种（胶囊 + 细边）；
 * - `dot` 给一个小圆点（状态灯用法），不靠"实心块"表达严重性。
 *
 * hub 那一层的 Badge（color: green/red/amber/cyan/slate）保留原 API，但实现改为委托本组件
 * —— 30+ 个 hub 调用点不用动，颜色照样收口到 token。
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type BadgeTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
export type BadgeSize = 'sm' | 'md'

/** 其余属性（data-testid / aria-* / id …）原样透传到 span */
export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** 语义色调，默认 neutral */
  tone?: BadgeTone
  /** 尺寸，默认 sm（10px） */
  size?: BadgeSize
  /** 左侧小圆点（状态灯用法） */
  dot?: boolean
  /** 圆点呼吸（进行中/告警未确认这类"还在动"的状态） */
  pulse?: boolean
  children?: ReactNode
}

const TONE: Record<BadgeTone, string> = {
  neutral: 'border-border bg-muted text-muted-foreground',
  primary: 'border-primary/40 bg-primary/15 text-primary',
  success: 'border-success/40 bg-success/15 text-success',
  warning: 'border-warning/40 bg-warning/15 text-warning',
  danger: 'border-danger/40 bg-danger/15 text-danger',
}

const SIZE: Record<BadgeSize, string> = {
  sm: 'px-1.5 py-0.5 text-micro',
  md: 'px-2 py-0.5 text-body',
}

const DOT: Record<BadgeTone, string> = {
  neutral: 'bg-muted-foreground',
  primary: 'bg-primary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

export default function Badge({
  tone = 'neutral',
  size = 'sm',
  dot = false,
  pulse = false,
  children,
  className,
  ...rest
}: BadgeProps) {
  return (
    <span
      {...rest}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full border font-medium',
        SIZE[size],
        TONE[tone],
        className,
      )}
    >
      {dot && (
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', DOT[tone], pulse && 'animate-pulse')} aria-hidden />
      )}
      {children}
    </span>
  )
}
