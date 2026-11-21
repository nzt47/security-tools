/**
 * Hub 通用 UI 组件：卡片 / 数据表格 / 徽章 / 加载 / 错误 / 空态
 *
 * 按钮不在这里自造：hub 页面的主/次/危险按钮统一走全局原语 components/ui/Button
 * （variant=primary 取语义主色 --color-primary，不再出现 bg-blue-600 这类第五种强调色）。
 * 仍从本文件导入，避免页面里同时出现两个 ui 命名空间。
 */
export { Button, Checkbox, CodeBlock, Input, ModalBase, Notice, Radio, RowButton, SectionTitle, Select, Switch, Slider, Tabs, Textarea } from '@/components/ui'

import type { ReactNode } from 'react'
// 全局 Badge 只在本文件内部使用（对外仍暴露 hub 那层带 color 枚举的 Badge）
import {
  Badge as ToneBadge,
  Card as UiCard,
  Loading as UiLoading,
  Notice,
} from '@/components/ui'
import { Loader2, AlertTriangle } from 'lucide-react'
import { getApiToken } from '../../../lib/apiToken'
import Table from '@/components/ui/Table'
import {
  callabilityColor,
  callabilityMark,
  callabilityTitle,
  type CallabilityInfo,
} from '../../../lib/callability'

/**
 * 面板卡片：标题 + 可选操作区 + 内容 —— **容器委托全局 ui/Card**（2026-11-18 封装审计）
 * 此前这里是 hub 自己的 slate 一套（rounded-xl / border-slate-800 / bg-slate-900/60），
 * 与全局 token 两套颜色；现在外壳走 ui/Card（border-border / bg-card / shadow-card），
 * 只在它上面加"标题 + 操作区"这一层（那是 hub 特有的排版，不是控件外观）。
 */
export function Card({
  title, actions, children, className = '',
}: {
  title?: string; actions?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <UiCard className={`overflow-hidden ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 className="text-body font-medium text-foreground">{title}</h3>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="p-4">{children}</div>
    </UiCard>
  )
}

/** 统计卡片（全景看板/仪表盘用） */
export function StatCard({
  label, value, unit, icon, color = 'text-cyan-400',
}: {
  label: string; value: ReactNode; unit?: string; icon?: ReactNode; color?: string
}) {
  return (
    <div className="rounded-card border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-caption text-muted-foreground">{label}</span>
        {icon && <span className={color}>{icon}</span>}
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="text-h1 font-semibold text-foreground">{value}</span>
        {unit && <span className="text-caption text-muted-foreground">{unit}</span>}
      </div>
    </div>
  )
}

/**
 * 通用数据表格 —— **薄封装全局 ui/Table**（2026-11-19 封装审计）
 * 【为什么改成委托】此前这里是 hub 自己的第二套表格实现（手写 thead/tbody + 自己一份
 * 列宽拖拽 + 自己一份空态），和全局 ui/Table 是两套外观。把「可拖列宽 / colStore /
 * rowProps / columns.title」补进 Table 之后，这里只负责 translate 老 API：
 * - `data` → `dataSource`、`keyField` → `rowKey`、`empty` → `emptyText`；
 * - 缺省单元格仍按 `String(row[key] ?? '')` 取值（Table 原语是 `String(row[key])`，
 *   直接透传会把缺字段渲染成字面量 "undefined"）；
 * - 嵌在 hub 的 Card 里 ⇒ `bordered={false}`（外层已经有一层卡片框，避免双层）。
 * 10 个页面的调用点一行不动。
 */
export function DataTable<T extends object>({
  columns, data, keyField, empty = '暂无数据', rowProps,
  resizable = false, colStore = 'yunshu.table.cols.v1', colWidths, minWidth,
}: {
  columns: { key: string; title: string; render?: (row: T) => ReactNode }[]
  data: T[]
  keyField: string
  empty?: string
  /** 逐行附加 DOM 属性（如 `data-focus-tool`）——供"从别处深链过来定位某一行"用 */
  rowProps?: (row: T) => Record<string, string>
  /**
   * 列宽可拖动（与技能中心同一份实现：`lib/tableCols` + `components/ColResizer`）。
   * 打开后表变 `table-fixed` + `colgroup`，宽度存 localStorage，双击手柄恢复该列默认值。
   */
  resizable?: boolean
  /** 列宽的 localStorage 键（**每张表一个**，互不串味） */
  colStore?: string
  /** 逐列默认宽度（px）；不传就按首列宽/其余等宽 */
  colWidths?: Record<string, number>
  /** 表格最小宽度（px）：窄屏时横向滚动，而不是把列挤成不可读 */
  minWidth?: number
}) {
  return (
    <Table<T>
      bordered={false}
      columns={columns.map((c) => ({
        key: c.key,
        title: c.title,
        render: c.render ?? ((row: T) => String((row as Record<string, unknown>)[c.key] ?? '')),
      }))}
      dataSource={data}
      rowKey={(row) => String((row as Record<string, unknown>)[keyField])}
      emptyText={empty}
      rowProps={rowProps}
      resizable={resizable}
      colStore={colStore}
      colWidths={colWidths}
      minWidth={minWidth}
    />
  )
}

/** 加载态 —— 委托全局 ui/Loading（行内形态与密度由 className 保留） */
export function Loading({ text = '加载中…' }: { text?: string }) {
  return <UiLoading text={text} className="flex-row py-12" />
}

/**
 * 错误态 —— **委托全局 ui/Notice**（2026-11-18 封装审计）
 * 【为什么改成委托】此前这里是 hub 自己的一套红框（border-red-900/60 + bg-red-950/40 +
 * text-red-400），与全局的语义 token 是两套颜色；而它被 48 个文件、104 处调用。
 * 改实现不改签名 ⇒ 104 处调用点一行不动，颜色统一由 --color-danger 决定。
 * 密度沿用原来的 px-4/py-3 + 14px（size 档之外用 className 表达，与 Button 同一口径）。
 */
export function ErrorBox({ message }: { message: string }) {
  return (
    <Notice tone="danger" className="px-4 py-3 text-ui" icon={<AlertTriangle size={16} />}>
      {message}
    </Notice>
  )
}

/** 徽章配色（Badge 与 CallabilityBadge 共用，避免两套色板漂移） */
/**
 * 状态徽章 —— 保持原来的 color API，但实现**委托**全局 ui/Badge。
 * 【2026-11-18】hub 调色板（green/red/amber/cyan/slate）此前写死成 tailwind 色名，
 * 与系统页的语义 token 徽章是两套；现在只保留这一层映射，颜色本身来自 index.css 的 token。
 */
/** 可调用性徽章的配色（与 Badge 的 tone 同源；保留一张小表是因为它要拼进 className 字符串） */
const BADGE_TONE_CLASS: Record<'green' | 'red' | 'amber' | 'cyan' | 'slate', string> = {
  green: 'border-success/40 bg-success/15 text-success',
  red: 'border-danger/40 bg-danger/15 text-danger',
  amber: 'border-warning/40 bg-warning/15 text-warning',
  cyan: 'border-primary/40 bg-primary/15 text-primary',
  slate: 'border-border bg-muted text-muted-foreground',
}

const BADGE_TONE: Record<'green' | 'red' | 'amber' | 'cyan' | 'slate', 'success' | 'danger' | 'warning' | 'primary' | 'neutral'> = {
  green: 'success',
  red: 'danger',
  amber: 'warning',
  cyan: 'primary',
  slate: 'neutral',
}

/**
 * hub 的 Badge：**两种入口都认**
 * - `color`（green/red/amber/cyan/slate）是老 API，30+ 个调用点不用动；
 * - `tone`（neutral/primary/success/warning/danger）与全局 ui/Badge 一致，新代码直接用。
 */
type HubBadgeColor = 'green' | 'red' | 'amber' | 'cyan' | 'slate'

export function Badge({
  color = 'slate',
  tone,
  size = 'md',
  children,
  ...rest
}: {
  color?: HubBadgeColor
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
  size?: 'sm' | 'md'
  children: ReactNode
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'children'>) {
  return (
    <ToneBadge tone={tone ?? BADGE_TONE[color]} size={size} {...rest}>
      {children}
    </ToneBadge>
  )
}

/**
 * 可调用性标识徽章（工具 / 技能「可被 LLM 调用」三档 ✅/⚠️/❌）
 *
 * 【退化语义】`info` 为空、`mark` 缺失（旧后端 `callability: {}` 或清单不可用）时
 * **不渲染任何东西**，也不占位 —— 界面与加标注之前完全一致。
 * 【悬浮说明】由 `lib/callability.ts::callabilityTitle` 组装多行文案（缺项不留空行）。
 * 【compact】装配预览的工具 chip 一行里已有 name + plane，再放整句标识会把 chip 撑得很长，
 * 故 chip 场景只显示字形（✅/⚠️/❌），完整文案仍在同一个 `title` 上。
 */
export function CallabilityBadge({ info, name, className = '', compact = false }: {
  info?: CallabilityInfo | null
  name?: string
  className?: string
  compact?: boolean
}) {
  const mark = callabilityMark(info)
  if (!mark) return null
  const title = callabilityTitle(info, name)
  const glyph = compact ? mark.split(/\s+/)[0] : mark
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 cursor-help items-center whitespace-nowrap rounded-full border ${compact ? 'px-1 py-0 text-micro' : 'px-2 py-0.5 text-body'} ${BADGE_TONE_CLASS[callabilityColor(mark)]} ${className}`}
    >
      {glyph}
    </span>
  )
}

/**
 * 叶子页「标题 + Tab 条」的唯一组合方式（2026-11-19，Owner 报「能力获取/引擎 排版不统一」）
 *
 * 【为什么单独立这一档】PageHeader 管标题的字号、Tabs 管页签的外观，但**谁在上面**
 * 没有机制约束：能力获取把 <Tabs> 写在 <PageHeader> 前面（标题落到 Tab 下面）；引擎 /
 * 实例与网络把 Tab 条当页头（压根没有标题）；各页又自己写 h1/h2 + text-sm/text-xs/
 * text-caption 的副标题 ⇒ 同一个「Tab 的小标题」在三个页面上是 14/12/11 三种字号。
 * 这里把「图标 + 标题 + 副标题 + 右侧动作 + Tab 条」收成一个组件：
 *   - 标题恒为 h1 text-title（16px）；
 *   - 副标题恒为 text-caption（11px），随激活 Tab 变化；
 *   - Tab 条恒在标题**下面**。
 * 新的带页签叶子页一律用它，不要再自己拼 <header> + <Tabs>。
 */
export function PageTabsHeader({ icon, title, hint, actions, tabs, className = '' }: {
  icon?: ReactNode
  title: string
  hint?: ReactNode
  actions?: ReactNode
  tabs: ReactNode
  className?: string
}) {
  return (
    <header className={`shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4 ${className}`}>
      {/* 【2026-11-21】标题行固定 h-6（24px）：否则页头动作里的控件高度（xs=24 / sm=28 /
          自定义链接=26）会把整行撑高，h1 的 y 在各页之间差 1~4px（真机 70/71/74）。
          固定后标题与页签条在所有叶子页同一 y。 */}
      <div className="mb-3 flex h-6 items-center gap-2">
        {icon}
        <h1 className="text-title font-semibold text-foreground">{title}</h1>
        {hint ? <span className="text-caption text-muted-foreground">{hint}</span> : null}
        {actions ? <div className="ml-auto flex items-center gap-1.5">{actions}</div> : null}
      </div>
      {tabs}
    </header>
  )
}


/** 页面标题（无页签的叶子页；带页签用 PageTabsHeader） */
export function PageHeader({ icon, title, description, actions }: {
  icon?: ReactNode
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-5 flex items-start justify-between">
      <div className="min-w-0">
        {/* 【2026-11-21】标题行固定 h-6（24px），与 PageTabsHeader 同一条基线：
            有图标/没图标的叶子，h1 的 y 与字号都一致（真机量过 70）。 */}
        <div className="flex h-6 items-center gap-2">
          {icon}
          <h1 className="text-title font-semibold text-foreground">{title}</h1>
        </div>
        {description && <p className="mt-1 text-caption text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** 把非 2xx 响应转成带后端原因的 Error（只抛 HTTP 状态会把后端的中文原因丢掉） */
async function _hubError(res: Response): Promise<Error> {
  let detail = ''
  try {
    const body = (await res.json()) as { error?: unknown; message?: unknown; errors?: unknown; detail?: unknown }
    // 【2026-11-22】校验类 400 回的是 {ok:false, errors:[...]}（没有 error 字段）——
    // 此前只读 error/message，界面只剩 "HTTP 400"，把真正的字段原因吞掉了。
    const raw = body?.error ?? body?.message
    if (typeof raw === 'string' && raw.trim()) {
      detail = raw.trim()
    } else if (Array.isArray(body?.errors)) {
      const parts = body.errors.map((x) => String(x).trim()).filter(Boolean)
      if (parts.length) detail = parts.join('；')
    } else if (typeof body?.detail === 'string' && body.detail.trim()) {
      // RFC 9457 的 detail（与 error 同义，兼容两种写法）
      detail = body.detail.trim()
    }
  } catch { /* 非 JSON 响应（HTML 错误页等）：保留状态码 */ }
  // 保留 "HTTP <code>" 作为子串：既有调用方/用例按它判读，不能改口径
  return new Error(detail ? detail + '（HTTP ' + res.status + '）' : 'HTTP ' + res.status)
}

/** 通用请求 hook：GET JSON，返回 {data, loading, error, reload} */
export async function hubGet<T = unknown>(url: string, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  // 未显式传令牌时自动附带本地保存的 API 令牌（受保护写接口 401 的集中根治）
  const auth = token !== undefined && token !== null ? token : getApiToken()
  if (auth) headers.Authorization = `Bearer ${auth}`
  const res = await fetch(url, { headers })
  if (!res.ok) throw await _hubError(res)
  return res.json() as Promise<T>
}

export async function hubPost<T = unknown>(url: string, body?: unknown, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  // 仅确有 body 时声明 JSON 内容类型：空 body + JSON 头会让 Flask get_json() 抛 400
  if (body !== undefined && body !== null) headers['Content-Type'] = 'application/json'
  const auth = token !== undefined && token !== null ? token : getApiToken()
  if (auth) headers.Authorization = `Bearer ${auth}`
  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await _hubError(res)
  return res.json() as Promise<T>
}

/**
 * DELETE JSON。
 *
 * 【为什么新增】2026-10-03 契约对拍（scripts/audit/contract_diff.py）实测检出 2 处
 * 前端用 hubPost 打了「路径不存在的 POST」：
 *   - /api/schedules/<id>/delete   （后端只有 DELETE /api/schedules/<task_id>）
 *   - /api/mcp/services/<id>/delete（后端只有 DELETE /api/mcp/services/<id>）
 * 两者**方法与路径都不对** ⇒ 运行时 405/404。此前没有 hubDelete，作者只能用 hubPost
 * 凑 —— 缺一个正确表达「删」的 helper，正是这类缺陷的温床。补上它。
 */
export async function hubDelete<T = unknown>(url: string, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  const auth = token !== undefined && token !== null ? token : getApiToken()
  if (auth) headers.Authorization = `Bearer ${auth}`
  const res = await fetch(url, { method: 'DELETE', headers })
  if (!res.ok) throw await _hubError(res)
  return res.json() as Promise<T>
}

/**
 * PUT JSON。
 *
 * 【为什么新增】记忆长期摘要、健康维度权重、告警规则、角色/用户、数据范围等一批
 * 后端端点只接受 **PUT**（语义是"整体替换这一份状态"）。此前页面要么干脆不接、
 * 要么借 hubPost 打成 POST —— 后者运行时必 405。helper 缺席时，作者只能凑；
 * 补一个能如实表达 PUT 的入口，比在每处写裸 fetch 更不容易写错。
 */
export async function hubPut<T = unknown>(url: string, body?: unknown, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined && body !== null) headers['Content-Type'] = 'application/json'
  const auth = token !== undefined && token !== null ? token : getApiToken()
  if (auth) headers.Authorization = `Bearer ${auth}`
  const res = await fetch(url, {
    method: 'PUT',
    headers,
    body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await _hubError(res)
  return res.json() as Promise<T>
}

/**
 * 带 JSON body 的 DELETE。
 *
 * 【为什么不能直接用 hubDelete】Flask 的 DELETE 也可以读 body（request.get_json()），
 * 而 /api/tools/keywords 的删除正是**靠 body 指定要删哪一条**
 * （{category, keyword}）。hubDelete 只发 URL，调用它会得到一个必然 400 的请求。
 * 【为什么不改 hubDelete 签名】现有三个调用方都是 hubDelete(url)，
 * 给第二个位置加 body 会让"传 token"的老写法静默变成"传 body"—— 这类静默错位最难查。
 */
export async function hubDeleteJson<T = unknown>(url: string, body?: unknown, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined && body !== null) headers['Content-Type'] = 'application/json'
  const auth = token !== undefined && token !== null ? token : getApiToken()
  if (auth) headers.Authorization = `Bearer ${auth}`
  const res = await fetch(url, {
    method: 'DELETE',
    headers,
    body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw await _hubError(res)
  return res.json() as Promise<T>
}

/** 将后端 {ok:true,data:...} 或 {code:200,data:...} 统一解包为 data */
export function unwrap<T>(resp: Record<string, unknown>): T {
  const r = resp as { ok?: boolean; data?: T; code?: number }
  if (r.data !== undefined) return r.data as T
  return resp as unknown as T
}

/**
 * 【K4 收口 · 2026-10-05 · P1-front 第七批】pickList / pickObj 两个**启发式**已删除。
 *
 * 它们做的是「在若干候选键里猜哪个是业务载荷」（pickList 找数组、pickObj 取 data 或整体），
 * 那不是契约：后端换一种包法，前端会**静默读错**而不是报错（本仓对此有多次记录）。
 * 随着 X-Envelope 铺开到全部被消费端点，调用点已逐个换成显式解析：
 *   · 自己发请求的页面 → src/api/envelope.ts 的 getEnvelope / postEnvelope；
 *   · 走 lib/apiClient.request() 的链路 → unwrapEnvelopeBody（拿已解析的体来拆）。
 *
 * 【为什么不留着「以防万一」】留着就等于把「猜」重新变成可选项 ——
 * 下一个新增页面会顺手用它，而它的失败模式是静默的。
 * 守卫：tests/unit/test_frontend_pick_helpers_removed.py（先剥注释再计数，必须为 0）。
 */