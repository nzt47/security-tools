/**
 * 工具豁免开关（把工具行上的「需确认」徽章变成可点的开关）
 * ------------------------------------------------------------------
 * 数据源：`GET/POST /api/cp/tool-exemptions`（见 lib/toolExemptionsApi.ts）。
 *
 * 【为什么状态收在一个 hook 里，而不是让它待在徽章组件内部】
 *   豁免是**全局生效**的（契约里没有主线参数），而同一个工具行在页面上出现两次
 *   （boost 选择器 / mute 选择器）。状态若留在行组件里，两处徽章会各说各话；
 *   收在单个 hook 里 + **后端返回的 exempt 列表当真值**，才能保证"点一处、两处同步、
 *   且与服务端一致"。
 *
 * 【为什么切换后要用返回值重绘，而不是就地翻转本地状态】
 *   后端完全可能因策略（settings_denied / locked_by_env）拒绝，或因需要二次确认而
 *   `changed:false`。就地翻转会把"没生效"画成"已生效"——界面撒谎比报错更糟，
 *   所以宁可牺牲一点点流畅感，也要以后端回的列表为唯一真值。
 *
 * 【为什么锁定态必须显示 blocked_reason】
 *   `exemptable=false` 的工具（shell_execute / run_sandbox / ext_install /
 *   generate_tool 这类描述符硬要求）点不动是设计如此，不是按钮坏了。
 *   不给原因，用户只会反复点，或者去报一个不存在的 bug。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Lock, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Badge, ErrorBox } from '../components/ui'
import {
  EXEMPTION_SOURCE_LABELS,
  exemptionErrorText,
  fetchToolExemptions,
  setToolExemption,
  type ToolExemptionItem,
} from '@/lib/toolExemptionsApi'

/** 任意一条豁免记录的处理结果（后端返回的 outcome）压缩成一行，避免刷屏 */
function shortOutcome(outcome: unknown): string {
  if (outcome === undefined || outcome === null) return '空'
  try {
    const text = JSON.stringify(outcome)
    if (!text) return '空'
    return text.length > 160 ? `${text.slice(0, 160)}…` : text
  } catch {
    return String(outcome)
  }
}

export type ExemptionFetchStatus = 'loading' | 'ready' | 'error'

export interface ToolExemptionState {
  status: ExemptionFetchStatus
  /** 读取豁免清单失败（code：message） */
  error: string
  /** 切换失败（含后端 changed=false 这类"没生效"） */
  actionError: string
  /** 切换成功提示 */
  notice: string
  items: ToolExemptionItem[]
  exempt: string[]
  source: string
  /** 后端给的人话来源（没有时由 UI 回落本地表） */
  sourceLabel: string
  /** 开关被环境变量锁住 ⇒ 界面改也会被后端拒（locked_by_env），必须提前讲清楚 */
  envLocked: boolean
  /** 后端回 changed=false 一类的"没改"说明（不是失败，但也不能当成功） */
  warning: string
  /** 正在提交的工具名；空串 = 空闲 */
  pendingTool: string
  lookup: (tool: string) => ToolExemptionItem | null
  isExempt: (tool: string) => boolean
  reload: () => void
  setExemption: (tool: string, exempt: boolean, reason?: string) => Promise<void>
}

export function useToolExemptions(): ToolExemptionState {
  const [items, setItems] = useState<ToolExemptionItem[]>([])
  const [exempt, setExempt] = useState<string[]>([])
  const [source, setSource] = useState('')
  const [sourceLabel, setSourceLabel] = useState('')
  const [envLocked, setEnvLocked] = useState(false)
  const [status, setStatus] = useState<ExemptionFetchStatus>('loading')
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [warning, setWarning] = useState('')
  const [notice, setNotice] = useState('')
  const [pendingTool, setPendingTool] = useState('')
  // 重试/连点时，旧请求可能后到：用序号丢弃过期响应，避免"后发先至"把新状态覆盖回去
  const seq = useRef(0)

  /** 视图落库：GET 与 POST 的回包形状一致（后端 200 时连 view 一起回），故共用同一段 */
  const applyView = useCallback((v: {
    items?: ToolExemptionItem[] | null
    exempt?: string[] | null
    source?: unknown
    source_label?: unknown
    env_locked?: unknown
  }) => {
    // exempt 是唯一真值：缺它就说明后端没按契约回，此时宁可不动，也不拿本地猜测顶上
    if (!Array.isArray(v.exempt)) return false
    setExempt(v.exempt)
    if (Array.isArray(v.items)) setItems(v.items)
    if (typeof v.source === 'string') setSource(v.source)
    if (typeof v.source_label === 'string') setSourceLabel(v.source_label)
    setEnvLocked(v.env_locked === true)
    return true
  }, [])

  const reload = useCallback(() => {
    const mine = ++seq.current
    setStatus('loading')
    fetchToolExemptions()
      .then((r) => {
        if (mine !== seq.current) return
        if (!applyView(r ?? {})) {
          setStatus('error')
          setError('后端未返回 exempt 列表（契约违规）：无法确定豁免真值')
          return
        }
        setStatus('ready')
        setError('')
      })
      .catch((e) => {
        if (mine !== seq.current) return
        setStatus('error')
        setError(exemptionErrorText(e))
      })
  }, [applyView])

  useEffect(() => { reload() }, [reload])

  const index = useMemo(
    () => new Map(items.map((it) => [it.tool, it])),
    [items],
  )
  const exemptSet = useMemo(() => new Set(exempt), [exempt])
  const lookup = useCallback((tool: string) => index.get(tool) ?? null, [index])
  const isExempt = useCallback((tool: string) => exemptSet.has(tool), [exemptSet])

  const setExemption = useCallback(async (tool: string, next: boolean, reason?: string) => {
    setPendingTool(tool)
    setActionError('')
    setWarning('')
    setNotice('')
    try {
      const r = await setToolExemption(tool, next, reason)
      if (!applyView(r ?? {})) {
        // 契约要求回传 exempt 列表；没有真值就宁可不改状态，也不能拿本地猜测顶上
        setActionError(`后端未返回 exempt 列表（契约违规）：本次${next ? '放宽' : '收紧'}结果未知，徽章保持原状`)
        return
      }
      if (r.changed === true) {
        setNotice(`已${next ? '放宽' : '收紧'}工具「${tool}」的人工确认要求（当前共豁免 ${r.exempt.length} 项）`)
      } else {
        // changed=false 在后端是**正常结论**（例如"已在豁免名单里，未重复写入"）：
        // 据此报红色错误会冤枉后端，静默又会掩盖"其实什么也没改"，故用中性提示 + 原话
        setWarning(
          `后端未改动任何内容（changed=false）：${r.message || `outcome=${shortOutcome(r.outcome)}`}。`
          + '徽章按后端返回的 exempt 列表重绘（未按本地猜测变化）。',
        )
      }
    } catch (e) {
      setActionError(`工具「${tool}」${next ? '放宽' : '收紧'}失败：${exemptionErrorText(e)}`)
    } finally {
      setPendingTool('')
    }
  }, [applyView])

  return {
    status, error, actionError, warning, notice,
    items, exempt, source, sourceLabel, envLocked, pendingTool,
    lookup, isExempt, reload, setExemption,
  }
}

/**
 * 行内开关徽章。
 *
 * 【三态】
 *   - 清单未读到（item 为空：旧后端 / 读取失败）⇒ 渲染成**原来那颗静态徽章**，
 *     页面不进降级页；
 *   - `exemptable=false` ⇒ 锁形态的 `<span>`（不是 button），title 给 blocked_reason；
 *   - 其余 ⇒ 真 `<button>`：点击在「需确认 ⇄ 已豁免」之间切换。
 *
 * 【为什么不用 <Badge> 包按钮】Badge 只接受 color/children，且行本身是大按钮，
 *   按钮不能嵌按钮 ⇒ 开关做成行按钮的**兄弟节点**（见 lines.tsx 的 ToolPicker）。
 */
export function ExemptionToggle({ name, item, exempt, pending, disabled, onToggle }: {
  name: string
  item: ToolExemptionItem | null
  /** 真值来自后端 exempt 列表（不是本地翻转） */
  exempt: boolean
  pending: boolean
  /** 有别的工具有请求在飞时一并禁用，避免并发提交把列表搅乱 */
  disabled: boolean
  onToggle: (tool: string, next: boolean) => void
}) {
  if (!item) {
    return (
      // 两种可能都不许猜：清单没读到，或该工具本来就不在豁免候选集里（后端只列 L0 以上的工具）
      <span
        data-exempt-unknown={name}
        title={`「${name}」不在已读到的豁免候选集里（清单未加载，或后端判定它无需豁免）：这里是只读标识，不是开关`}
      >
        <Badge color="amber">需确认</Badge>
      </span>
    )
  }

  if (!item.exemptable) {
    const why = item.blocked_reason || '该工具由描述符硬要求人工确认，豁免开关放不了它'
    return (
      <span
        data-exempt-locked={name}
        aria-disabled="true"
        title={`${why}（锁定：此开关放不了它）`}
        className="inline-flex shrink-0 cursor-not-allowed items-center gap-1 rounded-full border border-slate-700 bg-slate-500/15 px-2 py-0.5 text-xs text-slate-400"
      >
        <Lock size={10} />
        需确认（锁定）
      </span>
    )
  }

  const next = !exempt
  return (
    <button
      type="button"
      data-exempt-toggle={name}
      data-exempt={exempt ? 'exempt' : 'required'}
      disabled={disabled}
      title={exempt
        ? `「${name}」当前已豁免人工确认：点一下收紧，恢复为「需确认」（收紧不需要理由）`
        : `「${name}」当前需要人工确认：点一下放宽（全局生效，非仅本条主线），`
          + '会先问一次放宽原因并随请求记入审计'}
      onClick={(e) => {
        // 行本身是"选中该工具"的按钮：开关必须截断冒泡，否则点豁免会顺手改 boost/mute
        e.stopPropagation()
        onToggle(name, next)
      }}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${
        disabled
          ? 'cursor-not-allowed border-slate-700 bg-slate-500/15 text-slate-500'
          : exempt
            ? 'border-emerald-800 bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25'
            : 'border-amber-800 bg-amber-500/15 text-amber-400 hover:bg-amber-500/25'
      }`}
    >
      {pending
        ? <Loader2 size={10} className="animate-spin" />
        : exempt ? <ShieldCheck size={10} /> : <ShieldAlert size={10} />}
      {exempt ? '已豁免' : '需确认'}
    </button>
  )
}

/**
 * 豁免状态条：加载中 / 读取失败 / 空清单 / 已就绪 四种状态都必须有明确交代。
 *
 * 【为什么它必须与页面主错误分开】豁免接口是新端点，后端没上线时它必然报错；
 *   若把它混进页面 error，主线的档案/预览会被一起看成"坏了"。这里单独一条，
 *   读不到就退回静态徽章，主线管理照常可用。
 */
export function ExemptionStatusBar({ state, onReload }: {
  state: ToolExemptionState
  onReload: () => void
}) {
  const exemptable = state.items.filter((it) => it.exemptable).length
  const locked = state.items.length - exemptable
  // 优先用后端的人话来源；后端没给时回落本地表，再没有就原样显示 code（不编造含义）
  const sourceLabel = state.sourceLabel
    || EXEMPTION_SOURCE_LABELS[state.source]
    || state.source
    || '未知'

  return (
    <div className="mb-4 space-y-2">
      {state.status === 'loading' && (
        <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-2 text-xs text-slate-400">
          <Loader2 size={12} className="animate-spin" />
          正在读取工具豁免状态…
        </div>
      )}

      {state.status === 'error' && (
        <div className="flex flex-wrap items-center gap-3">
          <ErrorBox message={`工具豁免状态读取失败：${state.error}`} />
          <button
            onClick={onReload}
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            <RefreshCw size={12} /> 重试
          </button>
          <span className="text-[11px] text-slate-500">
            读取失败只影响豁免开关：徽章退回只读的「需确认」标识，主线档案与装配预览照常。
          </span>
        </div>
      )}

      {state.status === 'ready' && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-2 text-xs text-slate-400">
          <span className="flex items-center gap-1.5 text-slate-300">
            <ShieldAlert size={12} className="text-amber-400" /> 工具豁免
          </span>
          <span>已豁免 <b className="font-mono text-slate-200">{state.exempt.length}</b> 项</span>
          <span>可豁免 {exemptable}{locked > 0 ? ` / 锁定 ${locked}` : ''} 项</span>
          <span>来源：{sourceLabel}</span>
          <span className="text-slate-500">豁免为全局生效（不含主线维度），放宽会记入审计</span>
          {state.envLocked && (
            <span className="text-amber-400">
              该开关由环境变量锁定：这里的改动会被后端拒绝（locked_by_env），请改环境变量
            </span>
          )}
          <button
            onClick={onReload}
            className="ml-auto flex items-center gap-1.5 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
          >
            <RefreshCw size={11} /> 刷新
          </button>
        </div>
      )}

      {state.status === 'ready' && state.items.length === 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-2 text-[11px] text-slate-500">
          后端未返回任何可豁免条目：工具行上的「需确认」保持只读标识，页面其它功能不受影响。
        </div>
      )}

      {state.actionError && <ErrorBox message={state.actionError} />}

      {state.warning && (
        <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-4 py-2 text-xs text-amber-300">
          {state.warning}
        </div>
      )}

      {state.notice && (
        <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/30 px-4 py-2 text-xs text-emerald-300">
          {state.notice}
        </div>
      )}
    </div>
  )
}
