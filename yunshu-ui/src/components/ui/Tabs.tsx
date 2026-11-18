/**
 * Tabs —— 页签条（原生 role=tab 的语义，统一视觉）2026-11-18
 *
 * 【为什么补这一档】全仓有 12 处 hand-rolled `role="tab"` 按钮，分成**三种视觉家族**：
 *   · pill       —— 带边框的胶囊（DockPanel 工具条、能力获取页的四个页签）
 *   · segmented  —— 主色淡底的段控（记忆管理 hub 的 向量/知识库/经验库 切换）
 *   · folder     —— 上圆角的文件夹页签（工具集 / 分身页的页级页签、提示词实验室）
 * 三套各自写 className，选中态颜色还不一致（有的是主色描边、有的是主色淡底）。
 * 这里把三种家族收成 `variant`，选中态统一走 `--color-primary`。
 *
 * 口径：
 * - **元素仍是原生 button + role="tab"**（不做成 div）：键盘语义、aria-selected 由浏览器给出；
 * - 选中态=主色（描边或淡底），不用文字加粗当唯一区分；
 * - 计数徽标用 items[].count 传入，由本组件统一排版（不再每页各写一个 span）。
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type TabsVariant = 'pill' | 'segmented' | 'folder'
export type TabsSize = 'xs' | 'sm'

export interface TabItem {
  /** 值（受控） */
  value: string
  /** 文案（可含图标/计数） */
  label: ReactNode
  /** 可选计数徽标 */
  count?: ReactNode
  /** 悬浮提示（原页签常用 `hint` 解释这个子模块装什么） */
  title?: string
  /** 每项的 data-testid（页面/测试按它定位，如 tab-metrics） */
  testId?: string
  /** 禁用 */
  disabled?: boolean
}

export interface TabsProps {
  items: TabItem[]
  /** 当前值 */
  value: string
  onChange: (value: string) => void
  /** 视觉家族，默认 pill */
  variant?: TabsVariant
  /** 尺寸档，默认 sm */
  size?: TabsSize
  className?: string
  'aria-label'?: string
}

const BASE = 'inline-flex items-center gap-1.5 whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50'

const SIZE: Record<TabsSize, string> = {
  xs: 'px-2 py-1 text-micro',
  sm: 'px-3 py-1.5 text-body',
}

const VARIANT: Record<TabsVariant, { wrap: string; on: string; off: string }> = {
  pill: {
    wrap: 'flex flex-wrap items-center gap-1.5',
    on: 'rounded-control border border-primary/60 bg-primary/10 text-primary',
    off: 'rounded-control border border-border text-muted-foreground hover:bg-muted hover:text-foreground',
  },
  segmented: {
    wrap: 'flex flex-wrap items-center gap-1',
    on: 'rounded-control bg-primary/15 text-primary font-medium',
    off: 'rounded-control text-muted-foreground hover:bg-muted hover:text-foreground',
  },
  folder: {
    wrap: 'flex items-end gap-1.5 border-b border-border',
    // 选中态同样走主色（与 pill / segmented 一致 + index.css 的 [role=tab][aria-selected] 规则同向）
    on: 'rounded-t-panel border border-b-0 border-border bg-card text-primary font-medium',
    off: 'rounded-t-panel border border-b-0 border-transparent text-muted-foreground hover:bg-muted',
  },
}

export default function Tabs({
  items,
  value,
  onChange,
  variant = 'pill',
  size = 'sm',
  className,
  'aria-label': ariaLabel,
}: TabsProps) {
  const v = VARIANT[variant]
  return (
    <div role="tablist" aria-label={ariaLabel} className={cn(v.wrap, className)}>
      {items.map((it) => {
        const selected = it.value === value
        return (
          <button
            key={it.value}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={it.disabled}
            title={it.title}
            data-testid={it.testId}
            onClick={() => onChange(it.value)}
            className={cn(BASE, SIZE[size], selected ? v.on : v.off)}
          >
            {it.label}
            {it.count != null && (
              <span
                className={cn(
                  'rounded-full px-1.5 font-mono text-micro',
                  selected ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground',
                )}
              >
                {it.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
