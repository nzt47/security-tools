/**
 * 开关中心（TASK-S7-01）—— 治理面板内的第 8 个栏目
 * ==================================================================
 * 契约来源（FROZEN，后端并行实现）：
 *   - `GET  /api/cp/settings`                    → 登记表全量（≈300 项）+ counts/categories
 *   - `POST /api/cp/settings/<key>`              → 200 已生效 / 202 待第二位人工确认
 *   - `POST /api/cp/settings/<key>/confirm`      → 第二位人工确认后生效
 *   - `POST /api/cp/settings/<key>/reset`        → 回到 default
 *
 * 【本页面的四条纪律】
 *   ① **生效来源必须显式上屏**：每行都给出 `source` + `source_label`；
 *      `shadowed_by` 非空时说明"谁存在但没生效"（契约语义：低优先级来源被更高优先级覆盖）。
 *   ② **锁定就要说清原因**：`locked=true` ⇒ 控件禁用 + `locked_reason` **以文本形式**
 *      直接显示在行内（不是只塞进 tooltip）。
 *   ③ **机密只读**：`secret=true` 的行只渲染后端给的**已脱敏** `display_value` 与
 *      `configured` 徽章；没有密码框、没有"显示明文"入口，前端**不自建掩码串**。
 *   ④ **B 级不得直接提交**：先展开确认块（影响面 / 回滚方式 / 生效方式 + 二次认证），
 *      提交后若拿到 202，如实显示"待第二位人工确认"并用返回的 `pending_id` 走确认接口。
 *
 * 【口径纪律（§0.3）】所有数字要么来自 payload（`counts.*` / `categories[].count`），
 *   要么由 payload 的条目派生；缺位一律渲染 "—"，**绝不渲染 0**。
 *
 * 【为什么这里自带一个 Shell / 折叠组】`components.tsx` 没有导出折叠组件，
 *   `index.tsx` 的 `Collapsible` / `PanelShell` 是模块私有；本文件被 index.tsx
 *   反向导入，若把它们 export 出来会形成循环依赖，故按**同一套约定**
 *   （Loader2 加载中 / 红色错误条 / 刷新按钮）在此就地实现，不改动既有面板。
 */

import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react'
import {
  AlertTriangle, ChevronDown, ChevronRight, Clock, KeyRound, Loader2, Lock,
  RefreshCw, RotateCcw, Search, Unlock, X,
} from 'lucide-react'
import { debounce } from '@/lib/apiClient'
import {
  changeSetting,
  confirmSetting,
  fetchSettings,
  resetSetting,
} from '@/lib/cpPanelsApi'
import type {
  Metric,
  SettingsChangeResponse,
  SettingsItem,
  SettingsView,
} from '@/lib/cpPanelsTypes'
import { SwitchField } from '@/plugins/schema/fields/SwitchField'
import {
  AbsentBox,
  MetricValue,
  PanelHeader,
  StatusBadge,
  VirtualList,
  VIRTUAL_SCROLL_THRESHOLD,
  toneForRisk,
  type StatusTone,
} from './components'
import { usePanel } from './usePanel'

// ═══════════════════════════════════════════════════════════
//  常量与映射（**只做展示映射，不做业务判定**）
// ═══════════════════════════════════════════════════════════

/**
 * 风险 → 状态灯五态
 *
 * A 可直接切 ⇒ 绿（`toneForRisk('low')`）；B 需二次确认 ⇒ 黄（`toneForRisk('high')`);
 * C 只读脱敏 ⇒ 灰（只读，不该有"可操作"的暗示）。词表外的值仍交回既有 `toneForRisk`。
 */
export function toneForRiskLevel(risk: string | null | undefined): StatusTone {
  switch (String(risk || '')) {
    case 'A':
      return toneForRisk('low')
    case 'B':
      return toneForRisk('high')
    case 'C':
      return 'gray'
    default:
      // 词表外的取值（如上游给 low/high 等词）沿用既有映射，不自定义新态
      return toneForRisk(risk)
  }
}

/**
 * `shadowed_by` 里出现的低优先级来源的中文说明。
 *
 * 为什么需要：契约只给了**生效来源**的 `source_label`，没有给被覆盖来源的 label；
 * 而"谁存在但没生效"必须说人话。措辞**对齐后端** `resolver.SOURCE_LABELS`，
 * 避免同一来源出现两套说法（这四个键就是契约里 `source` 的封闭取值域）。
 */
const SOURCE_ZH: Record<string, string> = {
  env: '环境变量（运维注入）',
  ui_override: '开关中心覆盖层',
  config: 'config.yaml / 运行时配置',
  default: '代码默认值',
}

/** SwitchField 引用的 `--mascot-*` 变量只在未挂载的 theme.css 里定义 ⇒ 在此给回退值 */
const SWITCH_VARS = {
  '--mascot-primary': '#0e7490',
  '--mascot-error': '#f87171',
  '--border-color': '#334155',
  '--bg-elevated': '#0f172a',
} as unknown as CSSProperties

/** 虚拟滚动行高（仅在 >500 行的分组里启用） */
const ROW_HEIGHT = 136

/**
 * 风险筛选项
 *
 * 只显示契约的 A/B/C 与 payload 计数（`counts.total` / `counts.by_risk`）；
 * 风险的中文说法一律用后端 `risk_label`（行内徽章），芯片不另造措辞。
 */
const RISK_FILTERS: Array<{ id: 'all' | 'A' | 'B' | 'C'; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'A', label: 'A' },
  { id: 'B', label: 'B' },
  { id: 'C', label: 'C' },
]

// ═══════════════════════════════════════════════════════════
//  工具函数
// ═══════════════════════════════════════════════════════════

/**
 * 把后端的拒绝理由**原样**取出来（绝不改写成自己的措辞）
 *
 * 契约：401/403/400 体为 `{ ok:false, code, message, decision? }`；
 * `lib/apiClient.request()` 把整包塞在 `ApiError.details`，故优先取 `details.message`。
 */
export function settingsDenialMessage(e: unknown): string {
  const err = e as {
    message?: string
    details?: { message?: string; code?: string; decision?: { reason?: string } }
  }
  const body = err?.details
  return body?.message || body?.decision?.reason || err?.message || String(e)
}

/** 拒绝码（契约 `code`，仅用于展示；缺位则空串，不猜） */
export function settingsDenialCode(e: unknown): string {
  const err = e as { details?: { code?: string; decision?: { reason?: string } } }
  return err?.details?.code || ''
}

/** payload 计数 → 可追溯 Metric（缺位返回 null ⇒ MetricValue 渲染 "—"，不渲染 0） */
export function countMetric(
  value: number | null | undefined,
  field: string,
): Metric | null {
  if (value === null || value === undefined) return null
  return {
    value,
    available: true,
    source: `GET /api/cp/settings → counts.${field}`,
    formula: '',
    unit: 'count',
    dataset: '',
    note: '',
    traceable: true,
  }
}

/**
 * 客户端值转换（提交前的输入解析；**服务端校验仍是唯一裁决**）
 *
 * bool ⇒ 布尔；int/float ⇒ 数字；list/json ⇒ JSON（解析失败则按逗号切分）；其余按字符串。
 * 返回 `{ value, error }`：`error` 非空即**不发请求**（这是输入提示，不是后端拒绝理由）。
 */
export function coerceSettingValue(
  type: string,
  raw: string,
): { value: unknown; error: string } {
  const t = String(type || '').toLowerCase()
  const text = raw.trim()
  if (t === 'bool' || t === 'boolean') {
    if (['true', '1', 'on', 'yes'].includes(text.toLowerCase())) return { value: true, error: '' }
    if (['false', '0', 'off', 'no'].includes(text.toLowerCase())) return { value: false, error: '' }
    return { value: null, error: '请输入 true / false' }
  }
  if (t === 'int' || t === 'integer') {
    const n = Number(text)
    if (text === '' || !Number.isInteger(n)) return { value: null, error: '请输入整数' }
    return { value: n, error: '' }
  }
  if (t === 'float' || t === 'number' || t === 'double') {
    const n = Number(text)
    if (text === '' || Number.isNaN(n)) return { value: null, error: '请输入数字' }
    return { value: n, error: '' }
  }
  if (t === 'list' || t === 'array' || t === 'csv' || t === 'json') {
    try {
      return { value: JSON.parse(text), error: '' }
    } catch {
      return {
        value: text.split(',').map((s) => s.trim()).filter(Boolean),
        error: '',
      }
    }
  }
  return { value: raw, error: '' }
}

/** 该条目是否**必须先过确认块**（B 级 / 需要二次认证 / 需要双人） */
export function needsConfirmStep(item: SettingsItem): boolean {
  return item.risk === 'B' || item.requires_second_factor || item.requires_dual_approval
}

/** 需要确认的**理由**（逐条取自 payload 字段，不臆断） */
export function confirmReasons(item: SettingsItem): string[] {
  const reasons: string[] = []
  if (item.risk === 'B') reasons.push('风险 B 级')
  if (item.requires_second_factor) reasons.push('需二次认证')
  if (item.requires_dual_approval) reasons.push('需双人确认')
  return reasons.length > 0 ? reasons : ['需先确认']
}

/** 该条目是否存在可撤销的 `ui_override`（决定"重置"按钮是否出现/筛选口径） */
function hasOverride(item: SettingsItem): boolean {
  return item.source === 'ui_override' || (item.shadowed_by || []).includes('ui_override')
}

/**
 * 是否为"重置成功"回执
 *
 * 两侧形状都认：契约文档给顶层 `reset:true`；实现（service.ChangeOutcome）给
 * `receipt.reset === true`。
 */
export function isResetOutcome(resp: SettingsChangeResponse): boolean {
  return resp.reset === true || resp.receipt?.reset === true
}

/** 变更后的展示值：优先 `item.display_value`（实现形状），再退到顶层字段 */
export function outcomeDisplayValue(
  resp: SettingsChangeResponse,
  fallback?: SettingsItem,
): string {
  const fromItem = resp.item?.display_value
  if (fromItem !== undefined && fromItem !== '') return fromItem
  if (resp.display_value !== undefined && resp.display_value !== '') return resp.display_value
  if (resp.new !== undefined && resp.new !== null) return String(resp.new)
  return fallback?.display_value || '—'
}

// ═══════════════════════════════════════════════════════════
//  小件
// ═══════════════════════════════════════════════════════════

/** 面板壳（与 index.tsx 的 PanelShell 同一套 loading/error 约定） */
function SettingsShell({
  loading,
  error,
  reload,
  children,
}: {
  loading: boolean
  error: string
  reload: () => void
  children: ReactNode
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4">
      <div className="mb-2 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={reload}
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

/** 分类折叠组（带"筛选后 / 登记表"两个计数） */
function CategoryGroup({
  id,
  label,
  shown,
  declared,
  forceOpen,
  children,
}: {
  id: string
  label: string
  shown: number
  declared?: number
  forceOpen: boolean
  children: ReactNode
}) {
  // 默认展开：开关中心的价值首先是"看得见"；折叠用于快速收起噪音（筛选时强制展开）
  const [open, setOpen] = useState(true)
  useEffect(() => {
    if (forceOpen) setOpen(true)
  }, [forceOpen])
  const expanded = forceOpen || open
  return (
    <div
      className="mb-2 rounded-xl border border-slate-800 bg-slate-900/40"
      data-cp-settings-category={id}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-sm font-medium text-slate-200">{label}</span>
        <span className="text-[11px] text-slate-500" data-cp-category-count={id}>
          {shown} 项
          {declared !== undefined && declared !== shown && `（登记表 ${declared} 项）`}
        </span>
      </button>
      {expanded && <div className="border-t border-slate-800 p-2">{children}</div>}
    </div>
  )
}

/** 机密项：只渲染后端给的脱敏串 + 是否已配置（**没有输入框、没有 reveal**） */
function SecretValue({ item }: { item: SettingsItem }) {
  return (
    <div className="flex items-center gap-2" data-cp-secret-value={item.key}>
      <StatusBadge tone={item.configured ? 'green' : 'gray'}>
        {item.configured ? '已配置' : '未配置'}
      </StatusBadge>
      <span className="max-w-[220px] truncate font-mono text-[11px] text-slate-400">
        {item.display_value}
      </span>
    </div>
  )
}

/** 生效来源行（含 shadowed_by 说明） */
function SourceLine({ item }: { item: SettingsItem }) {
  const shadowed = item.shadowed_by || []
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span
        className="rounded border border-slate-700 bg-slate-950/60 px-1.5 py-0.5 text-[10px] text-slate-300"
        data-cp-setting-source={item.source}
        title={`生效来源 ${item.source}`}
      >
        来源：{item.source_label || item.source}
      </span>
      {/* 契约的四个来源 token 也直接可见，避免只有中文别名时无法对账 */}
      <span className="font-mono text-[10px] text-slate-500">{item.source}</span>
      {shadowed.length > 0 && (
        <span
          className="rounded border border-amber-900 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300"
          data-cp-shadowed-note={item.key}
        >
          当前被 {item.source_label || item.source}（{item.source}）覆盖：
          {shadowed.map((s) => `${SOURCE_ZH[s] || s}(${s})`).join('、')} 存在但未生效
        </span>
      )}
    </span>
  )
}

/** 单行渲染所需的全部状态与回调（**一个 prop 打包，避免十几次透传**） */
interface RowCtx {
  busyKey: string
  resetBusyKey: string
  editingKey: string
  drafts: Record<string, string>
  boolDrafts: Record<string, boolean>
  secondFactor: string
  reason: string
  feedback: Record<string, SettingsChangeResponse>
  errors: Record<string, string>
  onToggle: (item: SettingsItem, next: boolean) => void
  onOpenConfirm: (item: SettingsItem) => void
  onCloseConfirm: () => void
  onSubmitConfirm: (item: SettingsItem) => void
  onConfirmPending: (item: SettingsItem, pendingId: string) => void
  onReset: (item: SettingsItem) => void
  onDraft: (key: string, value: string) => void
  onBoolDraft: (key: string, value: boolean) => void
  onSecondFactor: (value: string) => void
  onReason: (value: string) => void
}

/** 单个开关行 */
function SettingRow({ item, ctx }: { item: SettingsItem; ctx: RowCtx }) {
  const busy = ctx.busyKey === item.key || ctx.resetBusyKey === item.key
  const locked = item.locked || !item.editable
  const confirmFirst = needsConfirmStep(item)
  const feedback = ctx.feedback[item.key]
  const error = ctx.errors[item.key]
  const editing = ctx.editingKey === item.key

  // ── 控件 ──────────────────────────────────────────────
  let control: ReactNode
  if (item.secret) {
    control = <SecretValue item={item} />
  } else if (item.type === 'bool') {
    control = (
      <div className="flex flex-col items-end gap-1">
        <SwitchField
          value={item.value === true}
          disabled={locked || busy || confirmFirst}
          onChange={(next) => ctx.onToggle(item, next)}
          label="当前值"
        />
        {item.display_value !== '' && (
          <span className="font-mono text-[10px] text-slate-500" data-cp-setting-value={item.key}>
            {item.display_value}
          </span>
        )}
      </div>
    )
  } else if (locked) {
    control = (
      <span className="font-mono text-[11px] text-slate-500" data-cp-setting-value={item.key}>
        {item.display_value}
      </span>
    )
  } else {
    control = (
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          aria-label={`${item.key} 的值`}
          data-cp-setting-input={item.key}
          value={ctx.drafts[item.key] ?? String(item.display_value ?? '')}
          onChange={(e) => ctx.onDraft(item.key, e.target.value)}
          disabled={busy || confirmFirst}
          className="w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200 disabled:opacity-50"
        />
      </div>
    )
  }

  return (
    <div
      className={`mb-1.5 rounded-lg border px-3 py-2 ${
        locked
          ? 'border-slate-800/80 bg-slate-950/40 opacity-60'
          : 'border-slate-800 bg-slate-900/50'
      }`}
      data-cp-setting-key={item.key}
      data-cp-setting-locked={String(item.locked)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {/* 标识行：key + 风险 + 生效来源 + 重启/仅 env 标记 */}
          <div className="flex flex-wrap items-center gap-1.5">
            <code className="font-mono text-xs text-cyan-300">{item.key}</code>
            <StatusBadge tone={toneForRiskLevel(item.risk)}>
              {item.risk_label || item.risk}
            </StatusBadge>
            <SourceLine item={item} />
            {item.needs_restart && (
              <span
                className="rounded border border-amber-800 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-300"
                data-cp-needs-restart={item.key}
              >
                需重启
              </span>
            )}
            {item.env_only && (
              <span
                className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400"
                data-cp-env-only={item.key}
              >
                仅支持环境变量
              </span>
            )}
            {item.locked && (
              <span
                className="inline-flex items-center gap-1 rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400"
                data-cp-locked={item.key}
              >
                <Lock size={9} /> 已锁定
              </span>
            )}
            {item.secret && (
              <span
                className="inline-flex items-center gap-1 rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400"
                data-cp-secret={item.key}
              >
                <KeyRound size={9} /> 只读脱敏
              </span>
            )}
          </div>

          {item.description && (
            <p className="mt-0.5 text-xs text-slate-400">{item.description}</p>
          )}

          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-slate-500">
            <span data-cp-default={item.key}>
              默认值：{item.default === null || item.default === undefined ? '—' : String(item.default)}
            </span>
            <span>生效方式：{item.effect_label || item.effect}</span>
            {item.config_path && <span className="font-mono">config: {item.config_path}</span>}
            {item.env_name && <span className="font-mono">env: {item.env_name}{item.env_present ? '（已设置）' : ''}</span>}
            {item.owner_module && <span className="font-mono">归属: {item.owner_module}</span>}
            <span className="font-mono">type: {item.type}</span>
          </div>

          {/* ① 锁定 ⇒ 原因必须**以文本**出现在行内 */}
          {item.locked && item.locked_reason && (
            <div
              className="mt-1 flex items-start gap-1 text-[11px] text-slate-400"
              data-cp-locked-reason={item.key}
            >
              <Lock size={10} className="mt-0.5 shrink-0" />
              <span>锁定原因：{item.locked_reason}</span>
            </div>
          )}

          {/* ② 机密只读说明（来源：后端 read_only_notice） */}
          {item.secret && item.masked && (
            <div className="mt-1 text-[10px] text-slate-500" data-cp-masked-note={item.key}>
              机密项：仅显示后端脱敏串（明文不返回，前端无重现入口）
            </div>
          )}

          {/* ③ 需要先确认再提交（B 级 / 需二次认证 / 需双人）：理由取自 payload 字段 */}
          {!item.secret && confirmFirst && !locked && (
            <div className="mt-1 flex items-center gap-2">
              <span className="text-[10px] text-amber-400/80">
                {confirmReasons(item).join(' + ')}：不能直接提交，请展开确认块
              </span>
              <button
                type="button"
                onClick={() => (editing ? ctx.onCloseConfirm() : ctx.onOpenConfirm(item))}
                disabled={busy}
                className="rounded border border-amber-800 px-1.5 py-0.5 text-[10px] text-amber-200 hover:bg-amber-500/10 disabled:opacity-50"
                data-cp-open-confirm={item.key}
              >
                {editing ? '收起确认' : '修改（需确认）'}
              </button>
            </div>
          )}

          {/* ④ 重置（仅有 ui_override 可撤时出现；就地调用 reset 端点） */}
          {!item.secret && hasOverride(item) && (
            <button
              type="button"
              onClick={() => ctx.onReset(item)}
              disabled={busy || !item.editable}
              className="mt-1 inline-flex items-center gap-1 rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 hover:text-cyan-300 disabled:opacity-40"
              data-cp-reset={item.key}
            >
              <RotateCcw size={9} />
              {ctx.resetBusyKey === item.key ? '重置中…' : '重置为默认'}
            </button>
          )}

          {error && (
            <div
              className="mt-1 rounded border border-red-900/60 bg-red-950/40 px-2 py-1 text-[11px] text-red-300"
              data-cp-error={item.key}
            >
              {error}
            </div>
          )}

          {feedback && feedback.pending && (
            <div
              className="mt-1 rounded border border-amber-800 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200"
              data-cp-pending={item.key}
            >
              <div className="flex items-center gap-1 font-medium">
                <Clock size={11} /> 待第二位人工确认（applied=false, pending=true）
              </div>
              {/* 后端原文，前端不改写 */}
              <div className="mt-0.5">{feedback.message}</div>
              <div className="mt-0.5 font-mono text-[10px]">
                pending_id：{feedback.pending_id || '—'}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <input
                  type="text"
                  aria-label={`${item.key} 确认二次认证码`}
                  data-cp-confirm-second-factor={item.key}
                  value={ctx.secondFactor}
                  onChange={(e) => ctx.onSecondFactor(e.target.value)}
                  placeholder="另一位人工的二次认证码"
                  className="w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200"
                />
                <input
                  type="text"
                  aria-label={`${item.key} 确认理由`}
                  data-cp-confirm-reason={item.key}
                  value={ctx.reason}
                  onChange={(e) => ctx.onReason(e.target.value)}
                  placeholder="确认理由"
                  className="w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200"
                />
                <button
                  type="button"
                  onClick={() => ctx.onConfirmPending(item, feedback.pending_id || '')}
                  disabled={busy || !feedback.pending_id}
                  className="inline-flex items-center gap-1 rounded border border-amber-700 px-2 py-0.5 text-[10px] text-amber-100 hover:bg-amber-500/10 disabled:opacity-50"
                  data-cp-confirm-submit={item.key}
                >
                  <Unlock size={10} /> 由另一位人工确认并生效
                </button>
              </div>
            </div>
          )}

          {feedback && feedback.applied && (
            <div
              className="mt-1 rounded border border-emerald-900 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300"
              data-cp-applied={item.key}
            >
              已生效：{item.key} → {outcomeDisplayValue(feedback, item)}
              （生效来源 {feedback.source || '—'}；
              {feedback.effect_label ? `生效方式 ${feedback.effect_label}` : ''}
              {feedback.audit?.seq !== undefined ? `；审计 #${feedback.audit.seq}` : ''}）
            </div>
          )}

          {feedback && isResetOutcome(feedback) && (
            <div
              className="mt-1 rounded border border-sky-900 bg-sky-500/10 px-2 py-1 text-[11px] text-sky-300"
              data-cp-reset-done={item.key}
            >
              已重置为默认：{item.key} → {outcomeDisplayValue(feedback, item)}
              （生效来源 {feedback.source || 'default'}）
            </div>
          )}

          {/* 未生效但成功的回执（如"该开关没有覆盖层记录，无需重置"）——后端原文 */}
          {feedback && !feedback.pending && !feedback.applied && !isResetOutcome(feedback) && (
            <div
              className="mt-1 rounded border border-slate-700 bg-slate-950/60 px-2 py-1 text-[11px] text-slate-300"
              data-cp-noop={item.key}
            >
              {feedback.message || '后端未返回可读回执'}
            </div>
          )}

          {/* ⑤ 提交前确认块（影响面 / 回滚方式 / 生效方式 + 二次认证） */}
          {editing && (
            <div
              className="mt-2 space-y-1 rounded border border-amber-900/70 bg-slate-950/70 px-2 py-2"
              data-cp-confirm-panel={item.key}
            >
              <div className="text-[11px] font-medium text-amber-200">
                提交前确认（{item.risk_label || item.risk}）
              </div>
              {item.impact ? (
                <div className="text-[11px] text-slate-300" data-cp-impact={item.key}>
                  影响面：{item.impact}
                </div>
              ) : (
                <AbsentBox label="影响面" reason="后端未提供 impact" />
              )}
              {item.rollback ? (
                <div className="text-[11px] text-slate-300" data-cp-rollback={item.key}>
                  回滚方式：{item.rollback}
                </div>
              ) : (
                <AbsentBox label="回滚方式" reason="后端未提供 rollback" />
              )}
              <div className="text-[11px] text-slate-300" data-cp-effect={item.key}>
                生效方式：{item.effect_label || item.effect}
              </div>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                {item.type === 'bool' ? (
                  <div className="w-32">
                    <SwitchField
                      value={ctx.boolDrafts[item.key] ?? item.value === true}
                      onChange={(next) => ctx.onBoolDraft(item.key, next)}
                      label="提交值"
                    />
                  </div>
                ) : (
                  <input
                    type="text"
                    aria-label={`${item.key} 提交值`}
                    data-cp-confirm-value={item.key}
                    value={ctx.drafts[item.key] ?? String(item.display_value ?? '')}
                    onChange={(e) => ctx.onDraft(item.key, e.target.value)}
                    className="w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-[11px] text-slate-200"
                  />
                )}
                <input
                  type="text"
                  aria-label={`${item.key} 二次认证码`}
                  data-cp-second-factor={item.key}
                  value={ctx.secondFactor}
                  onChange={(e) => ctx.onSecondFactor(e.target.value)}
                  placeholder="二次认证码"
                  className="w-36 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200"
                />
                <input
                  type="text"
                  aria-label={`${item.key} 变更理由`}
                  data-cp-reason={item.key}
                  value={ctx.reason}
                  onChange={(e) => ctx.onReason(e.target.value)}
                  placeholder="变更理由（审计留痕）"
                  className="w-44 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200"
                />
                <button
                  type="button"
                  onClick={() => ctx.onSubmitConfirm(item)}
                  disabled={busy}
                  className="rounded bg-cyan-800 px-2 py-1 text-[11px] text-cyan-50 hover:bg-cyan-700 disabled:opacity-50"
                  data-cp-submit={item.key}
                >
                  提交变更
                </button>
                <button
                  type="button"
                  onClick={ctx.onCloseConfirm}
                  disabled={busy}
                  className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 disabled:opacity-50"
                  data-cp-cancel={item.key}
                >
                  取消
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="shrink-0">{control}</div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  面板主体
// ═══════════════════════════════════════════════════════════

export interface SettingsPanelProps {
  /** 搜索防抖窗口（毫秒）；默认 250，测试可调小 */
  searchDebounceMs?: number
}

/**
 * 开关中心
 *
 * 结构与数据流：
 *   一次 `GET /api/cp/settings` ⇒ 客户端筛选（关键词 + 风险 + 只看已覆盖）
 *   ⇒ 按 `categories` 顺序分组折叠 ⇒ 行内编辑（A 直切 / B 先确认）
 *   ⇒ 写动作走 `POST /api/cp/settings/<key>`，第二人走 `/confirm`，撤销走 `/reset`。
 */
export function SettingsPanel({ searchDebounceMs = 250 }: SettingsPanelProps) {
  const { data, loading, error, reload } = usePanel<SettingsView>(
    () => fetchSettings(),
    [],
  )

  const [query, setQuery] = useState('')
  const [needle, setNeedle] = useState('')
  const [risk, setRisk] = useState<'all' | 'A' | 'B' | 'C'>('all')
  const [onlyOverride, setOnlyOverride] = useState(false)

  const [busyKey, setBusyKey] = useState('')
  const [resetBusyKey, setResetBusyKey] = useState('')
  const [editingKey, setEditingKey] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [boolDrafts, setBoolDrafts] = useState<Record<string, boolean>>({})
  const [secondFactor, setSecondFactor] = useState('')
  const [reason, setReason] = useState('')
  const [feedback, setFeedback] = useState<Record<string, SettingsChangeResponse>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  // 防抖：复用 lib/apiClient 的 debounce（不引第三方）
  const commitSearch = useMemo(
    () => debounce((value: string) => setNeedle(value), searchDebounceMs),
    [searchDebounceMs],
  )

  const items = useMemo(() => data?.items || [], [data])
  const categories = useMemo(() => data?.categories || [], [data])

  // ── 客户端筛选 ─────────────────────────────────────────
  const filtered = useMemo(() => {
    const q = needle.trim().toLowerCase()
    return items.filter((it) => {
      if (risk !== 'all' && it.risk !== risk) return false
      // "已覆盖" 与行内"重置为默认"用同一判定：存在 ui_override（生效或存在但被覆盖）
      if (onlyOverride && !hasOverride(it)) return false
      if (!q) return true
      return [
        it.key,
        it.description,
        it.category,
        it.category_label,
        it.source_label,
        it.owner_module,
      ]
        .join(' ')
        .toLowerCase()
        .includes(q)
    })
  }, [items, needle, risk, onlyOverride])

  // ── 分组（顺序取 payload 的 categories，未登记的类别追加在后）──
  const groups = useMemo(() => {
    const byCat = new Map<string, SettingsItem[]>()
    filtered.forEach((it) => {
      const id = it.category || 'uncategorized'
      const arr = byCat.get(id)
      if (arr) arr.push(it)
      else byCat.set(id, [it])
    })
    const declaredOrder = categories.map((c) => c.id)
    const ids = [
      ...declaredOrder.filter((id) => byCat.has(id)),
      ...[...byCat.keys()].filter((id) => !declaredOrder.includes(id)),
    ]
    return ids.map((id) => {
      const rows = byCat.get(id) as SettingsItem[]
      const meta = categories.find((c) => c.id === id)
      return {
        id,
        label: meta?.label || rows[0]?.category_label || id,
        declared: meta?.count,
        rows,
      }
    })
  }, [filtered, categories])

  // ── 写动作 ─────────────────────────────────────────────
  const submitValue = async (item: SettingsItem, value: unknown) => {
    setBusyKey(item.key)
    setErrors((prev) => ({ ...prev, [item.key]: '' }))
    try {
      const resp = await changeSetting(item.key, {
        value,
        second_factor: secondFactor || undefined,
        reason: reason || undefined,
      })
      setFeedback((prev) => ({ ...prev, [item.key]: resp }))
      setEditingKey('')
      // 只有"真的生效了"才回读登记表（202 待确认时保持现场，等第二人确认）
      if (resp.applied) reload()
    } catch (e) {
      const code = settingsDenialCode(e)
      setErrors((prev) => ({
        ...prev,
        [item.key]: code ? `[${code}] ${settingsDenialMessage(e)}` : settingsDenialMessage(e),
      }))
    } finally {
      setBusyKey('')
    }
  }

  const onSubmitConfirm = (item: SettingsItem) => {
    if (item.type === 'bool') {
      void submitValue(item, boolDrafts[item.key] ?? item.value === true)
      return
    }
    const coerced = coerceSettingValue(item.type, drafts[item.key] ?? String(item.display_value ?? ''))
    if (coerced.error) {
      const message = coerced.error
      setErrors((prev) => ({ ...prev, [item.key]: message }))
      return
    }
    void submitValue(item, coerced.value)
  }

  const onConfirmPending = async (item: SettingsItem, pendingId: string) => {
    setBusyKey(item.key)
    setErrors((prev) => ({ ...prev, [item.key]: '' }))
    try {
      const resp = await confirmSetting(item.key, {
        pending_id: pendingId,
        second_factor: secondFactor || undefined,
        reason: reason || undefined,
      })
      setFeedback((prev) => ({ ...prev, [item.key]: resp }))
      if (resp.applied) reload()
    } catch (e) {
      const code = settingsDenialCode(e)
      setErrors((prev) => ({
        ...prev,
        [item.key]: code ? `[${code}] ${settingsDenialMessage(e)}` : settingsDenialMessage(e),
      }))
    } finally {
      setBusyKey('')
    }
  }

  const onReset = async (item: SettingsItem) => {
    setResetBusyKey(item.key)
    setErrors((prev) => ({ ...prev, [item.key]: '' }))
    try {
      const resp = await resetSetting(item.key)
      setFeedback((prev) => ({ ...prev, [item.key]: resp }))
      reload()
    } catch (e) {
      const code = settingsDenialCode(e)
      setErrors((prev) => ({
        ...prev,
        [item.key]: code ? `[${code}] ${settingsDenialMessage(e)}` : settingsDenialMessage(e),
      }))
    } finally {
      setResetBusyKey('')
    }
  }

  const onOpenConfirm = (item: SettingsItem) => {
    setEditingKey(item.key)
    setDrafts((prev) => ({ ...prev, [item.key]: String(item.display_value ?? '') }))
    setBoolDrafts((prev) => ({ ...prev, [item.key]: item.value === true }))
  }

  const ctx: RowCtx = {
    busyKey,
    resetBusyKey,
    editingKey,
    drafts,
    boolDrafts,
    secondFactor,
    reason,
    feedback,
    errors,
    onToggle: (item, next) => void submitValue(item, next),
    onOpenConfirm,
    onCloseConfirm: () => setEditingKey(''),
    onSubmitConfirm,
    onConfirmPending: (item, pendingId) => void onConfirmPending(item, pendingId),
    onReset: (item) => void onReset(item),
    onDraft: (key, value) => setDrafts((prev) => ({ ...prev, [key]: value })),
    onBoolDraft: (key, value) => setBoolDrafts((prev) => ({ ...prev, [key]: value })),
    onSecondFactor: setSecondFactor,
    onReason: setReason,
  }

  const counts = data?.counts
  const filtering = needle.trim() !== '' || risk !== 'all' || onlyOverride

  if (data && !data.ok) {
    return (
      <SettingsShell loading={false} error="开关登记表不可用（ok=false）" reload={reload}>
        <span />
      </SettingsShell>
    )
  }

  return (
    <SettingsShell loading={loading} error={error} reload={reload}>
      {data && (
        <div style={SWITCH_VARS} data-cp-settings-root="true">
          <PanelHeader
            title="开关中心"
            description="后端登记的每一个开关：生效来源 / 风险分级 / 生效方式 / 锁定原因；B 级须先确认再提交"
            datasources={[
              'GET /api/cp/settings',
              'POST /api/cp/settings/<key>',
              'POST /api/cp/settings/<key>/confirm',
              'POST /api/cp/settings/<key>/reset',
            ]}
          />

          {/* ── 计数（全部来自 payload.counts；缺位渲染 — 不渲染 0）── */}
          <div
            className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-xs"
            data-cp-settings-counts="true"
          >
            <MetricValue metric={countMetric(counts?.total, 'total')} label="登记总数" />
            <MetricValue metric={countMetric(counts?.editable, 'editable')} label="可改" />
            <MetricValue metric={countMetric(counts?.locked, 'locked')} label="锁定" />
            <MetricValue metric={countMetric(counts?.overridden, 'overridden')} label="已覆盖" />
            <span className="text-[10px] text-slate-600">
              生成时间（后端）：{data.generated_at || '—'}
            </span>
            {/* 优先级顺序取自 payload.source_priority（前端不自造） */}
            {data.source_priority && data.source_priority.length > 0 && (
              <span
                className="font-mono text-[10px] text-slate-500"
                data-cp-source-priority="true"
              >
                来源优先级（高→低）：{data.source_priority.join(' > ')}
              </span>
            )}
          </div>

          {data.read_only_notice && (
            <div className="mb-2 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-1.5 text-[11px] text-slate-400">
              {data.read_only_notice}
            </div>
          )}

          {/* ── 搜索 + 风险筛选 ── */}
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1.5 text-slate-500" />
              <input
                type="text"
                aria-label="搜索开关"
                data-cp-settings-search="true"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                  commitSearch(e.target.value)
                }}
                placeholder="搜索 key / 描述 / 分类 / 归属模块"
                className="w-72 rounded border border-slate-700 bg-slate-950 py-1 pl-6 pr-6 text-xs text-slate-200 placeholder:text-slate-600"
              />
              {query !== '' && (
                <button
                  type="button"
                  aria-label="清空搜索"
                  onClick={() => {
                    setQuery('')
                    commitSearch('')
                  }}
                  className="absolute right-1.5 top-1 text-slate-500 hover:text-slate-300"
                >
                  <X size={12} />
                </button>
              )}
            </div>

            {RISK_FILTERS.map((f) => {
              const active = risk === f.id
              const value =
                f.id === 'all' ? counts?.total : counts?.by_risk?.[f.id]
              return (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setRisk(f.id)}
                  aria-pressed={active}
                  data-cp-risk-filter={f.id}
                  className={`rounded-full border px-2 py-0.5 text-[11px] ${
                    active
                      ? 'border-cyan-700 bg-cyan-500/15 text-cyan-200'
                      : 'border-slate-700 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {f.label}
                  {value !== undefined && <span className="ml-1 text-slate-500">{value}</span>}
                </button>
              )
            })}

            <button
              type="button"
              onClick={() => setOnlyOverride((v) => !v)}
              aria-pressed={onlyOverride}
              data-cp-override-filter="true"
              title="按『存在 ui_override（生效，或存在但被更高优先级来源覆盖）』筛选；数字为 payload.counts.overridden"
              className={`rounded-full border px-2 py-0.5 text-[11px] ${
                onlyOverride
                  ? 'border-cyan-700 bg-cyan-500/15 text-cyan-200'
                  : 'border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              只看已覆盖
              {counts?.overridden !== undefined && (
                <span className="ml-1 text-slate-500">{counts.overridden}</span>
              )}
            </button>

            <span className="text-[10px] text-slate-500">
              命中 {filtered.length} / 登记 {items.length} 项
              <span className="ml-1 text-slate-600">（登记表条目数，非估计值）</span>
            </span>
          </div>

          {/* ── 分组列表 ── */}
          {filtered.length === 0 ? (
            <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-xs text-slate-500">
              <Search size={13} />
              <span>
                当前筛选下没有开关（登记表共 {items.length} 项；本页不做"补 0"占位）
              </span>
              {filtering && (
                <button
                  type="button"
                  onClick={() => {
                    setQuery('')
                    commitSearch('')
                    setRisk('all')
                    setOnlyOverride(false)
                  }}
                  className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 hover:text-cyan-300"
                  data-cp-clear-filters="true"
                >
                  清除筛选
                </button>
              )}
            </div>
          ) : (
            groups.map((g) => {
              // 虚拟滚动仅用于 >500 行的分组，且该组没有展开中的行（避免定高裁切确认块）
              const expandedHere = g.rows.some(
                (it) =>
                  editingKey === it.key ||
                  Boolean(feedback[it.key]) ||
                  Boolean(errors[it.key]),
              )
              const virtual = g.rows.length > VIRTUAL_SCROLL_THRESHOLD && !expandedHere
              return (
                <CategoryGroup
                  key={g.id}
                  id={g.id}
                  label={g.label}
                  shown={g.rows.length}
                  declared={g.declared}
                  forceOpen={filtering}
                >
                  {virtual ? (
                    <VirtualList
                      items={g.rows}
                      itemHeight={ROW_HEIGHT}
                      height={560}
                      keyOf={(it) => it.key}
                      renderItem={(it) => <SettingRow item={it} ctx={ctx} />}
                    />
                  ) : (
                    <div>
                      {g.rows.map((it) => (
                        <SettingRow key={it.key} item={it} ctx={ctx} />
                      ))}
                    </div>
                  )}
                </CategoryGroup>
              )
            })
          )}
        </div>
      )}
    </SettingsShell>
  )
}
