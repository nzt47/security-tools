/**
 * 治理面板通用组件（P7.2-24 组件名清单的前端落点）
 * ------------------------------------------------------------------
 * 导出：
 *   - `StatusBadge`：状态灯**五态**（灰/蓝/黄/绿/红）★ U6 性能实测对象
 *   - `MetricValue`：可追溯数字（值 + "来源"入口 + "仅披露"角标）
 *   - `VirtualList`：虚拟滚动（阈值 500，UI 五坑②：别全量实时渲染）
 *   - `ReasonChain`："它现在在想什么"可解释性（**非原始 JSON**）
 *   - `TaintBadge`：外来文本恒带底色徽章（class 名来自后端常量）
 *   - `ApprovalZone`：审批按钮区 DOM 隔离（Shadow DOM + 固定 z-index）
 *   - `Collapsible` / `PanelShell`：折叠组与面板外壳
 *
 * 【Collapsible / PanelShell 为什么落在这里（TASK-S7-01）】
 *   两者原先定义在 `index.tsx` 内部（模块私有）。开关中心 `settings.tsx` 需要同一套
 *   外壳，而 `settings.tsx` 被 `index.tsx` 静态导入——若让 `settings.tsx` 反向
 *   import `index.tsx` 会形成**循环依赖**；若各自实现一份，又会留下"平行副本"
 *   （S7-01 复核确实先复制了一份，随后删掉）。故按"共享 UI 原语放共享模块"的口径
 *   **上移到本文件**：`index.tsx` 与 `settings.tsx` 都从 `./components` 引用，
 *   **既无重复、也无环**。
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle, ChevronDown, ChevronRight, CircleSlash, Info, Loader2,
  RefreshCw, ShieldAlert, Siren,
} from 'lucide-react'
import { useSecurityState, formatNumber, formatPercent } from './usePanel'
import type { Metric } from '@/lib/cpPanelsTypes'

/**
 * 折叠区（P1/P2 默认收起；P0 默认展开）
 *
 * `forceOpen`：外部强制展开（开关中心在搜索/筛选时要求分组展开）；
 * 默认 `false`，既有调用行为不变。
 */
export function Collapsible({
  title, subtitle, defaultOpen = false, icon, children, forceOpen = false,
}: {
  title: string
  subtitle?: string
  defaultOpen?: boolean
  icon?: ReactNode
  children: ReactNode
  /** 强制展开（为真时以展开为准） */
  forceOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const expanded = forceOpen || open
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {icon}
        <span className="text-sm font-medium text-slate-200">{title}</span>
        {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
      </button>
      {expanded && <div className="border-t border-slate-800 p-4">{children}</div>}
    </div>
  )
}

/** 面板外壳（加载中 / 错误条 / 刷新按钮） */
export function PanelShell({
  loading, error, reload, children, onRefresh,
}: {
  loading: boolean
  error: string
  reload: () => void
  children: ReactNode
  onRefresh?: () => void
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4">
      <div className="mb-2 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onRefresh || reload}
          className="flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-400 hover:text-cyan-300"
        >
          <RefreshCw size={11} /> 刷新
        </button>
      </div>
      {loading && (
        <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
          <Loader2 size={16} className="animate-spin" /> 加载中…
        </div>
      )}
      {!loading && error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          <AlertTriangle size={15} className="mt-0.5" />
          <span className="break-all">{error}</span>
        </div>
      )}
      {!loading && !error && children}
    </div>
  )
}

/** 虚拟滚动阈值（§11.2 / UI 五坑②：≥500 条必须虚拟滚动） */
export const VIRTUAL_SCROLL_THRESHOLD = 500

// ═══════════════════════════════════════════════════════════
//  StatusBadge：状态灯五态（灰/蓝/黄/绿/红）
// ═══════════════════════════════════════════════════════════

/** 五态（§7 逐字：灰/蓝/黄/绿/红；**不得增删态**） */
export type StatusTone = 'gray' | 'blue' | 'yellow' | 'green' | 'red'

const STATUS_TONE_CLASS: Record<StatusTone, string> = {
  gray: 'bg-slate-500/15 text-slate-300 border-slate-600',
  blue: 'bg-sky-500/15 text-sky-300 border-sky-700',
  yellow: 'bg-amber-500/15 text-amber-300 border-amber-700',
  green: 'bg-emerald-500/15 text-emerald-300 border-emerald-700',
  red: 'bg-red-500/15 text-red-300 border-red-700',
}

const STATUS_DOT_CLASS: Record<StatusTone, string> = {
  gray: 'bg-slate-400',
  blue: 'bg-sky-400',
  yellow: 'bg-amber-400',
  green: 'bg-emerald-400',
  red: 'bg-red-400',
}

/**
 * 状态灯
 *
 * ★ U6：本组件的"状态变更 → 首帧重绘"耗时**随本任务实测**（见
 * `StatusBadge.perf.test.tsx` 与 `docs/PERF_BUDGET_REBASED.md` §五 回填）。
 * 为此组件刻意保持无副作用、无请求、无重排依赖：纯 props → 一次提交。
 */
export function StatusBadge({
  tone,
  children,
  pulse = false,
  title,
}: {
  tone: StatusTone
  children: ReactNode
  pulse?: boolean
  title?: string
}) {
  return (
    <span
      data-cp-status-tone={tone}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${STATUS_TONE_CLASS[tone]}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT_CLASS[tone]} ${
          pulse ? 'animate-pulse' : ''
        }`}
      />
      {children}
    </span>
  )
}

/** 由后端 stage / 判定词映射到五态（**只做展示映射，不做业务判定**） */
export function toneForStage(stage: string | null | undefined): StatusTone {
  switch (String(stage || '')) {
    case 'borrowed':
      return 'gray'
    case 'mirrored':
      return 'blue'
    case 'shadow':
      return 'yellow'
    case 'internalized':
      return 'green'
    case 'native':
      return 'green'
    case 'permanent_borrowed':
      return 'gray'
    case 'deprecated':
      return 'red'
    default:
      return 'gray'
  }
}

export function toneForRisk(risk: string | null | undefined): StatusTone {
  switch (String(risk || '')) {
    case 'destructive':
      return 'red'
    case 'high':
      return 'yellow'
    case 'medium':
      return 'blue'
    case 'low':
      return 'green'
    default:
      return 'gray'
  }
}

export function toneForSeverity(severity: string | null | undefined): StatusTone {
  switch (String(severity || '')) {
    case 'L5':
      return 'red'
    case 'L4':
      return 'red'
    case 'L3':
      return 'yellow'
    case 'L2':
      return 'blue'
    case 'L1':
      return 'green'
    default:
      return 'gray'
  }
}

// ═══════════════════════════════════════════════════════════
//  MetricValue：可追溯数字（§0.3 纪律③的前端落点）
// ═══════════════════════════════════════════════════════════

/**
 * 指标展示
 *
 * 规则（**不得放宽**）：
 *   - `value === null` ⇒ 渲染 `—` 且标注"数据源缺位"（**绝不渲染 0**）；
 *   - `insufficient_sample` ⇒ 渲染"仅披露"角标（样本 < 20 不考核）；
 *   - 每个数字都有"来源"入口（点击展开 `source` / `formula` / `dataset` / 样本量）。
 */
export function MetricValue({
  metric,
  label,
  format = 'number',
  digits,
  unit,
  className = '',
}: {
  metric: Metric | null | undefined
  label?: string
  format?: 'number' | 'percent' | 'cents' | 'ms' | 'text'
  digits?: number
  unit?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (!metric) {
    return <span className={className}>—</span>
  }
  const absent = metric.value === null || metric.value === undefined
  let text: string
  switch (format) {
    case 'percent':
      text = formatPercent(metric.value, digits ?? 1)
      break
    case 'cents':
      text = absent ? '—' : `¥${((metric.value as number) / 100).toFixed(2)}`
      break
    case 'ms':
      text = absent
        ? '—'
        : `${(metric.value as number).toFixed((metric.value as number) < 10 ? 3 : 1)} ms`
      break
    case 'text':
      text = absent ? '—' : String(metric.value)
      break
    default:
      text = formatNumber(metric.value, {
        digits,
        unit: unit ?? (metric.unit || undefined),
      })
  }

  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      {label && <span className="text-slate-500">{label}</span>}
      <span
        className={absent ? 'text-slate-500' : 'font-medium text-slate-200'}
        data-cp-metric-value={absent ? 'absent' : String(metric.value)}
      >
        {text}
      </span>
      {metric.insufficient_sample && (
        <span
          title={`${metric.note || '样本不足'}（样本 ${metric.sample_size ?? 0} < ${metric.min_sample ?? 20}）`}
          className="rounded border border-amber-800 bg-amber-500/10 px-1 text-[10px] text-amber-300"
        >
          仅披露
        </span>
      )}
      <button
        type="button"
        aria-label="来源与口径"
        onClick={() => setOpen((v) => !v)}
        className="text-slate-600 transition-colors hover:text-cyan-400"
      >
        <Info size={11} />
      </button>
      {open && (
        <span className="ml-1 block w-full rounded border border-slate-800 bg-slate-950/80 px-2 py-1 font-mono text-[10px] leading-relaxed text-slate-400">
          <span className="block">来源：{metric.source || '（未标注，禁止上屏）'}</span>
          {metric.formula && <span className="block">公式：{metric.formula}</span>}
          {metric.dataset && <span className="block">数据集：{metric.dataset}</span>}
          {metric.sample_size !== undefined && (
            <span className="block">
              样本：{metric.sample_size}（阈值 {metric.min_sample ?? 20}）
            </span>
          )}
          {metric.note && <span className="block text-amber-400/80">口径：{metric.note}</span>}
        </span>
      )}
    </span>
  )
}

/** 缺位占位（后端 `absent()` 的渲染） */
export function AbsentBox({ reason, label }: { reason?: string; label?: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-xs text-slate-500">
      <CircleSlash size={13} />
      <span>
        {label || '数据源缺位'}：{reason || '未提供'}
        <span className="ml-1 text-slate-600">（记 None，不以 0 冒充）</span>
      </span>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  VirtualList：虚拟滚动（阈值 500）
// ═══════════════════════════════════════════════════════════

/**
 * 极简定高虚拟滚动（**不引第三方依赖**，对齐仓库既有 500 节点手写做法）
 *
 * 为什么必须：UI 五坑②"别全量实时渲染"——≥500 条一次性挂 DOM 会让首帧掉帧。
 */
export function VirtualList<T>({
  items,
  itemHeight,
  height,
  renderItem,
  keyOf,
  overscan = 4,
  className = '',
}: {
  items: T[]
  itemHeight: number
  height: number
  renderItem: (item: T, index: number) => ReactNode
  keyOf: (item: T, index: number) => string
  overscan?: number
  className?: string
}) {
  const [scrollTop, setScrollTop] = useState(0)
  const ref = useRef<HTMLDivElement | null>(null)

  const { start, end } = useMemo(() => {
    const first = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan)
    const visible = Math.ceil(height / itemHeight) + overscan * 2
    return { start: first, end: Math.min(items.length, first + visible) }
  }, [scrollTop, itemHeight, height, items.length, overscan])

  const virtualized = items.length > VIRTUAL_SCROLL_THRESHOLD

  if (!virtualized) {
    return (
      <div className={className} data-cp-virtualized="false">
        {items.map((item, i) => (
          <div key={keyOf(item, i)} style={{ minHeight: itemHeight }}>
            {renderItem(item, i)}
          </div>
        ))}
      </div>
    )
  }

  const slice = items.slice(start, end)
  return (
    <div
      ref={ref}
      className={`overflow-y-auto ${className}`}
      style={{ height }}
      data-cp-virtualized="true"
      data-cp-total={items.length}
      onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}
    >
      <div style={{ height: items.length * itemHeight, position: 'relative' }}>
        <div style={{ transform: `translateY(${start * itemHeight}px)` }}>
          {slice.map((item, i) => (
            <div key={keyOf(item, start + i)} style={{ height: itemHeight }}>
              {renderItem(item, start + i)}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  ReasonChain：可解释性（"它现在在想什么"，非原始 JSON）
// ═══════════════════════════════════════════════════════════

export interface ReasonNode {
  label: string
  value?: ReactNode
  detail?: string
  passed?: boolean
  /** 证据来源（可追溯） */
  source?: string
}

/**
 * 推理链（P7.2-24 可解释性）
 *
 * 呈现的是**结构化结论**：每一步的判断、实测值、阈值、证据来源；
 * 而不是把后端 JSON 直接扔到页面上（那等于没有可解释性）。
 */
export function ReasonChain({
  title,
  nodes,
  formula,
  defaultOpen = false,
}: {
  title: string
  nodes: ReasonNode[]
  formula?: string
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-300 hover:bg-slate-800/40"
      >
        <ChevronRight
          size={13}
          className={`transition-transform ${open ? 'rotate-90' : ''}`}
        />
        <span className="font-medium">{title}</span>
        {formula && (
          <span className="ml-auto font-mono text-[10px] text-slate-600">{formula}</span>
        )}
      </button>
      {open && (
        <ol className="space-y-1.5 border-t border-slate-800 px-3 py-2">
          {nodes.map((n, i) => (
            <li key={`${n.label}-${i}`} className="flex items-start gap-2 text-xs">
              <span className="mt-0.5 font-mono text-[10px] text-slate-600">
                {String(i + 1).padStart(2, '0')}
              </span>
              {n.passed !== undefined && (
                <span
                  className={
                    n.passed
                      ? 'mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400'
                      : 'mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-red-400'
                  }
                />
              )}
              <span className="min-w-0 flex-1">
                <span className="text-slate-300">{n.label}</span>
                {n.value !== undefined && (
                  <span className="ml-1.5 text-slate-100">{n.value}</span>
                )}
                {n.detail && (
                  <span className="ml-1.5 text-slate-500">{n.detail}</span>
                )}
                {n.source && (
                  <span className="mt-0.5 block font-mono text-[10px] text-slate-600">
                    证据：{n.source}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  TaintBadge：外来文本恒带底色徽章（class 名来自后端常量）
// ═══════════════════════════════════════════════════════════

/**
 * 污染徽章（§5.7 机制 6）
 *
 * ★ U1：class 名**必须**取自 `safe_render_state()["taint_badge"]`；
 *   拿不到常量时**拒绝渲染徽章**（宁可没有徽章，也不自定义一个看起来像的）。
 *   样式由 `cpTaintBadgeStyle` 按后端 class 名动态注入一次（见 styles）。
 */
export function TaintBadge({ source, label }: { source?: string; label?: string }) {
  const { state } = useSecurityState()
  const badge = state?.safe_render?.taint_badge
  if (!badge) return null // 常量缺位 ⇒ 不渲染（U1）
  return (
    <span
      className={`${badge.class} ${badge.base_class} inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]`}
      {...{ [badge.attr]: source || 'foreign' }}
      title={`外来内容（不可信来源）：${source || '未标注'}`}
    >
      <ShieldAlert size={10} />
      {label || '外来内容'}
    </span>
  )
}

// ═══════════════════════════════════════════════════════════
//  ApprovalZone：审批按钮区 DOM 隔离（Shadow DOM + 固定 z-index）
// ═══════════════════════════════════════════════════════════

/**
 * 审批按钮区（§5.7⑦ + P7.2-24）
 *
 * ★ U1/U2：**Shadow DOM 自治单元 + 固定 z-index**——
 *   宿主页面的 CSS（含 Tailwind）不进入影子树，因此：
 *     - 容器样式取自 `approval_zone.style`（position/z-index/isolation/pointer-events）；
 *     - 影子树内部样式由本组件内联注入（**不依赖 Tailwind**）；
 *     - `z-index` **不得**由主题/内容覆盖（后端给的是固定值）。
 *
 * 这就是「前端审批区统一挂载到工作台」的落点：审批区不再是 Flask 侧游离页面，
 * 而是工作台里的一个 Shadow DOM 自治单元（Shadow DOM 保留）。
 */
export function ApprovalZone({
  children,
  inline = false,
}: {
  children: ReactNode
  /** `inline`：嵌在面板内（跟随流布局）；默认 fixed 悬浮（沿用既有审批台位置感） */
  inline?: boolean
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [mount, setMount] = useState<HTMLDivElement | null>(null)
  const { state } = useSecurityState()
  const zone = state?.safe_render?.approval_zone

  useEffect(() => {
    const host = hostRef.current
    if (!host || !zone) return
    if (host.shadowRoot) return
    const root = host.attachShadow({ mode: 'open' })
    const style = document.createElement('style')
    // 影子树内部样式（**不引 Tailwind**：影子树拿不到宿主样式表）
    style.textContent = `
      .cp-zone-inner { font-family: inherit; color: #e2e8f0;
        background: rgba(2,6,23,.96); border: 1px solid #1e293b;
        border-radius: 12px; padding: 12px; display: flex;
        flex-direction: column; gap: 8px; }
      .cp-zone-inner button { font: inherit; border-radius: 6px; padding: 4px 10px;
        border: 1px solid #334155; background: #0f172a; color: #e2e8f0;
        cursor: pointer; }
      .cp-zone-inner button[data-primary="true"] { background: #0e7490; border-color: #0e7490; }
      .cp-zone-inner button:disabled { opacity: .5; cursor: not-allowed; }
    `
    root.appendChild(style)
    const node = document.createElement('div')
    node.className = 'cp-zone-inner'
    root.appendChild(node)
    setMount(node)                     // 触发一次重渲染，让 portal 有真实挂载点
  }, [zone])

  const style: CSSProperties = useMemo(() => {
    const raw = zone?.style || {}
    const base: CSSProperties = {
      zIndex: Number(raw['z-index'] || 2147483000),
      isolation: (raw['isolation'] || 'isolate') as CSSProperties['isolation'],
      pointerEvents: (raw['pointer-events'] || 'auto') as CSSProperties['pointerEvents'],
      contain: 'layout style paint',
    }
    if (inline) return { ...base, position: 'relative' }
    return { ...base, position: 'fixed', right: 20, bottom: 20, width: 380 }
  }, [zone, inline])

  // 常量缺位 ⇒ 拒绝渲染审批按钮区（U1：不自定义 z-index）
  if (!zone) return null

  return (
    <div
      ref={hostRef}
      className={zone.class}
      style={style}
      data-cp-approval-zone="true"
      data-cp-z-index={zone.style['z-index']}
      data-cp-shadow-root={String(zone.shadow_root)}
    >
      {mount ? createPortal(children, mount) : null}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  小件
// ═══════════════════════════════════════════════════════════

/** 事故级告警条（自愈面板用） */
export function IncidentBanner({ count, note }: { count: number; note: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
      <Siren size={14} />
      <span>
        当前有 <strong>{count}</strong> 起未闭合事故（status=open）⇒ 面板自动展开
        <span className="ml-1 text-red-400/70">{note}</span>
      </span>
    </div>
  )
}

/** 面板头（标题 + 优先级 + 数据源入口） */
export function PanelHeader({
  title,
  description,
  priority,
  datasources,
  actions,
}: {
  title: string
  description?: string
  priority?: string
  datasources?: string[]
  actions?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const tone: StatusTone =
    priority === 'P0' ? 'red' : priority === 'P1' ? 'yellow' : 'gray'
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          {priority && (
            <StatusBadge tone={tone} title="面板优先级（§7：P0 展开 / P1·P2 折叠）">
              {priority}
            </StatusBadge>
          )}
          {datasources && datasources.length > 0 && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="text-[10px] text-slate-500 underline decoration-dotted hover:text-cyan-400"
            >
              数据源（{datasources.length}）
            </button>
          )}
        </div>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
        {open && datasources && (
          <ul className="mt-1.5 space-y-0.5">
            {datasources.map((d) => (
              <li key={d} className="font-mono text-[10px] text-slate-500">
                · {d}
              </li>
            ))}
          </ul>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
