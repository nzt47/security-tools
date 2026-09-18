/**
 * Hub 通用 UI 组件：卡片 / 数据表格 / 徽章 / 加载 / 错误 / 空态
 */
import type { ReactNode } from 'react'
import { Loader2, AlertTriangle, Inbox } from 'lucide-react'
import { getApiToken } from '../../../lib/apiToken'
import {
  callabilityColor,
  callabilityMark,
  callabilityTitle,
  type CallabilityInfo,
} from '../../../lib/callability'

/** 面板卡片：标题 + 可选操作区 + 内容 */
export function Card({
  title, actions, children, className = '',
}: {
  title?: string; actions?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/60 ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-medium text-slate-200">{title}</h3>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  )
}

/** 统计卡片（全景看板/仪表盘用） */
export function StatCard({
  label, value, unit, icon, color = 'text-cyan-400',
}: {
  label: string; value: ReactNode; unit?: string; icon?: ReactNode; color?: string
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500">{label}</span>
        {icon && <span className={color}>{icon}</span>}
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="text-2xl font-semibold text-white">{value}</span>
        {unit && <span className="text-xs text-slate-500">{unit}</span>}
      </div>
    </div>
  )
}

/** 通用数据表格 */
export function DataTable<T extends object>({
  columns, data, keyField, empty = '暂无数据',
}: {
  columns: { key: string; title: string; render?: (row: T) => ReactNode }[]
  data: T[]
  keyField: string
  empty?: string
}) {
  if (data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-10 text-slate-500">
        <Inbox size={24} />
        <span className="text-sm">{empty}</span>
      </div>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
            {columns.map((c) => (
              <th key={c.key} className="px-3 py-2 font-medium">{c.title}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row) => {
            const r = row as Record<string, unknown>
            return (
              <tr key={String(r[keyField])} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                {columns.map((c) => (
                  <td key={c.key} className="px-3 py-2.5 text-slate-300">
                    {c.render ? c.render(row) : String(r[c.key] ?? '')}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 加载态 */
export function Loading({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-slate-400">
      <Loader2 size={18} className="animate-spin" />
      <span className="text-sm">{text}</span>
    </div>
  )
}

/** 错误态 */
export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-400">
      <AlertTriangle size={16} />
      <span>{message}</span>
    </div>
  )
}

/** 徽章配色（Badge 与 CallabilityBadge 共用，避免两套色板漂移） */
const BADGE_CLASS = {
  green: 'bg-emerald-500/15 text-emerald-400 border-emerald-800',
  red: 'bg-red-500/15 text-red-400 border-red-800',
  amber: 'bg-amber-500/15 text-amber-400 border-amber-800',
  cyan: 'bg-cyan-500/15 text-cyan-400 border-cyan-800',
  slate: 'bg-slate-500/15 text-slate-400 border-slate-700',
}

/** 状态徽章 */
export function Badge({ color = 'slate', children }: { color?: 'green' | 'red' | 'amber' | 'cyan' | 'slate'; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${BADGE_CLASS[color]}`}>
      {children}
    </span>
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
      className={`inline-flex shrink-0 cursor-help items-center whitespace-nowrap rounded-full border ${compact ? 'px-1 py-0 text-[10px]' : 'px-2 py-0.5 text-xs'} ${BADGE_CLASS[callabilityColor(mark)]} ${className}`}
    >
      {glyph}
    </span>
  )
}

/** 页面标题 */
export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex items-start justify-between">
      <div>
        <h1 className="text-xl font-semibold text-white">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** 通用请求 hook：GET JSON，返回 {data, loading, error, reload} */
export async function hubGet<T = unknown>(url: string, token?: string | null): Promise<T> {
  const headers: Record<string, string> = {}
  // 未显式传令牌时自动附带本地保存的 API 令牌（受保护写接口 401 的集中根治）
  const auth = token !== undefined && token !== null ? token : getApiToken()
  if (auth) headers.Authorization = `Bearer ${auth}`
  const res = await fetch(url, { headers })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
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
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

/** 将后端 {ok:true,data:...} 或 {code:200,data:...} 统一解包为 data */
export function unwrap<T>(resp: Record<string, unknown>): T {
  const r = resp as { ok?: boolean; data?: T; code?: number }
  if (r.data !== undefined) return r.data as T
  return resp as unknown as T
}

/**
 * 从后端响应中提取数组列表（兼容多种返回结构）：
 * - {data: [...]} / {data: {list: [...]}}
 * - {sessions/items/cards/tasks/subagents/tools/tools_list: [...]}
 * - 直接返回数组
 */
export function pickList<T = Record<string, unknown>>(resp: unknown, prefer?: string): T[] {
  const r = resp as Record<string, unknown>
  if (Array.isArray(resp)) return resp as T[]
  if (!r || typeof r !== 'object') return []
  // 优先取 data
  const d = r.data
  if (Array.isArray(d)) return d as T[]
  if (d && typeof d === 'object') {
    const dl = (d as Record<string, unknown>).list
    if (Array.isArray(dl)) return dl as T[]
  }
  // 常见列表字段
  const keys = prefer ? [prefer] : ['sessions', 'items', 'cards', 'tasks', 'subagents', 'tools', 'tools_list', 'list', 'logs', 'history', 'results', 'installed', 'available', 'menus', 'records']
  for (const k of keys) {
    const v = r[k]
    if (Array.isArray(v)) return v as T[]
    if (v && typeof v === 'object' && Array.isArray((v as Record<string, unknown>).list)) {
      return (v as Record<string, unknown>).list as T[]
    }
  }
  return []
}

/** 从后端响应中提取对象（兼容 data 包裹） */
export function pickObj<T = Record<string, unknown>>(resp: unknown): T | null {
  if (!resp || typeof resp !== 'object') return null
  const r = resp as Record<string, unknown>
  if (r.data && typeof r.data === 'object' && !Array.isArray(r.data)) return r.data as T
  return r as T
}
