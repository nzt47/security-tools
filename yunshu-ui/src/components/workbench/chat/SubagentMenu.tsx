/**
 * SubagentMenu —— 会话任务区「子代理」下拉
 * ------------------------------------------------------------------
 * 需求：在 UI 的会话任务区域添加子代理显示下拉菜单按钮，用于展示和选择
 * 当前任务委托的子代理。
 *
 * 数据源（**两个**，各回答一个问题）：
 *   - 活跃分身：GET /api/subagent/list（P4 分身生命周期；agent.orchestrator.SubagentManager）
 *     —— 回答「**能委托给谁**」。注意：委派一律 destroy_after=True（跑完即回收），
 *     所以这个列表在正常业务里**可能为空**；为空不等于功能坏了。
 *   - 委派记录：GET /api/subagent/history（agent/subagent/delegation_history.py）
 *     —— 回答「**发生过什么**」。每次真委派（模型工具 / 批量 / 界面）落一条轻量记录，
 *     与分身是否还活着无关（这正是线上反馈"有业务发生却什么都看不到"的修法）。
 *
 * 能力：
 *   - 展示活跃分身（名称 / 模型 / 记忆提供方 / 上下文占用 / TTL 与存活时间）；
 *   - **选择**当前任务委托的子代理（LocalStorage 持久化，按钮上回显所选名称）；
 *     【陈旧选择】localStorage 里的名字可能已随分身回收而消失 —— 此时显式提示并提供
 *     「清除选择」，而不是让它一路走到 404「分身不存在」；
 *   - 「委托执行」：把任务交给所选分身执行（POST /api/subagent/<name>/delegate），
 *     任务内容默认预填当前会话最近一条用户消息（fail-soft，可编辑）；
 *   - 「复用目标」：把历史记录里的目标回填到输入框，省掉重打一遍。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, Check, ChevronDown, ChevronRight, Loader2, Play, RefreshCw, UserPlus, X } from 'lucide-react'
import { hubGet } from '../../../pages/hub/components/ui'
import { authHeader } from '../../../lib/apiToken'

/** 当前任务委托的子代理（LocalStorage 持久化；跨会话保留用户选择） */
const DELEGATE_KEY = 'yunshu.subagent.delegate'

/** 临时分身委派端点（不要求先存在分身：现建现用、跑完即回收，与模型侧 delegate 工具同链路） */
const EPHEMERAL_DELEGATE_URL = '/api/subagent/delegate'

interface Subagent {
  id?: string
  name: string
  model_id?: string
  memory_provider?: string
  tags?: string[]
  context_window?: number
  context_used?: number
  ttl_seconds?: number
  age_seconds?: number
  is_expired?: boolean
  created_at?: string
  tool_sources?: string[]
}

/** 委派执行通道可用性（后端 /api/subagent/list 的 channel 字段） */
interface ChannelInfo {
  llm?: boolean
  cli?: boolean
  agent_cli?: string
  ok?: boolean
}

/** 真委派结果（后端 /api/subagent/<name>/delegate 的响应，形状与模型侧 delegate 工具一致） */
interface DelegateResult {
  ok?: boolean
  name?: string
  delegation_id?: string
  tier?: string
  duration_ms?: number
  trace_id?: string
  result?: string
  artifact_count?: number
  error_code?: string
  error?: string
  sub_reason?: string
}

/** 一条委派记录（GET /api/subagent/history 的 records 元素；**不含交付物正文**） */
interface DelegationRecord {
  delegation_id?: string
  subagent?: string
  source?: string
  ok?: boolean
  tier?: string
  goal?: string
  duration_ms?: number
  trace_id?: string
  error_code?: string
  error?: string
  artifact_count?: number
  tokens?: number | null
  created_at?: string
}

/** 委派入口的中文标注（后端 source 字段；空串 = 未标注） */
const SOURCE_LABEL: Record<string, string> = {
  ui: '界面',
  tool: '模型工具',
  fan_out: '批量委派',
  lifecycle: '内部',
}

const sourceLabel = (s?: string) => (s ? SOURCE_LABEL[s] ?? s : '未标注')

/** 记录时间戳 → 本地时间（后端给的是带时区的 ISO8601；解析失败则原样截断显示） */
function fmtRecordTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 19).replace('T', ' ')
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function readDelegate(): string {
  try {
    return localStorage.getItem(DELEGATE_KEY) ?? ''
  } catch {
    return ''
  }
}

const STATUS_TEXT = (sa: Subagent) => {
  if (sa.is_expired) return { text: '已过期', cls: 'text-rose-400' }
  if ((sa.age_seconds ?? 0) > 0) return { text: '活跃', cls: 'text-emerald-400' }
  return { text: '就绪', cls: 'text-slate-400' }
}

export function SubagentMenu({ defaultTask = '' }: { defaultTask?: string }) {
  const [open, setOpen] = useState(false)
  const [list, setList] = useState<Subagent[]>([])
  const [channel, setChannel] = useState<ChannelInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [delegate, setDelegate] = useState<string>(readDelegate)
  const [records, setRecords] = useState<DelegationRecord[]>([])
  const [recordsTotal, setRecordsTotal] = useState<number | null>(null)
  const [recordsError, setRecordsError] = useState('')
  /** 首次拉取是否已完成：陈旧选择的告警必须等列表真的到了再说，否则开面板瞬间会闪一下假告警 */
  const [loaded, setLoaded] = useState(false)
  const [task, setTask] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<DelegateResult | null>(null)
  // 八要素中的可选项（真委派链路要求；留空则用后端文档缺省补齐）
  const [advanced, setAdvanced] = useState(false)
  const [constraints, setConstraints] = useState('')
  const [prohibitions, setProhibitions] = useState('')
  const [artifactFormat, setArtifactFormat] = useState('')
  const [budgetTokens, setBudgetTokens] = useState('')
  const [timeoutSeconds, setTimeoutSeconds] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await hubGet<{
        ok?: boolean
        subagents?: Subagent[]
        count?: number
        channel?: ChannelInfo
        error?: string
      }>('/api/subagent/list')
      if (r?.ok === false) {
        setError(String(r.error ?? '分身列表查询失败'))
        setList([])
      } else {
        setList(Array.isArray(r?.subagents) ? r.subagents : [])
        setChannel(r?.channel ?? null)
      }
    } catch (e) {
      // 分身系统未启用（subagent.enabled=False）时接口报错属正常，给出可执行提示
      setError(`子代理列表不可用：${e instanceof Error ? e.message : String(e)}（请确认后端已启动且 subagent.enabled=true）`)
      setList([])
    } finally {
      setLoading(false)
      setLoaded(true)
    }
  }, [])

  /** 拉委派记录：与「能委托给谁」相互独立，失败只提示、不清空主流程 */
  const loadHistory = useCallback(async () => {
    try {
      const r = await hubGet<{
        ok?: boolean
        records?: DelegationRecord[]
        total?: number | null
        error?: string
      }>('/api/subagent/history?limit=20')
      if (r?.ok === false) {
        setRecordsError(String(r.error ?? '委派记录查询失败'))
      } else {
        setRecords(Array.isArray(r?.records) ? r.records : [])
        setRecordsTotal(typeof r?.total === 'number' ? r.total : null)
        setRecordsError('')
      }
    } catch (e) {
      // 记录是附带信息：拉不到不影响"选择 + 委托"主流程，故只提示、不清空已加载的数据
      setRecordsError(`委派记录不可用：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [])

  // 首次展开时加载（活跃分身 + 委派记录）；关闭后保留已加载数据
  useEffect(() => {
    if (!open) return
    void load()
    void loadHistory()
  }, [open, load, loadHistory])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const pick = (name: string) => {
    setDelegate(name)
    try {
      localStorage.setItem(DELEGATE_KEY, name)
    } catch {
      /* localStorage 不可用仅内存态 */
    }
  }

  /** 清除陈旧选择（localStorage 里的名字已不在活跃列表） */
  const clearDelegate = () => {
    setDelegate('')
    try {
      localStorage.removeItem(DELEGATE_KEY)
    } catch {
      /* localStorage 不可用仅内存态 */
    }
  }

  const splitList = (v: string) =>
    v.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)

  const liveNames = list.map((s) => s.name)
  /** 已保存的选择是否仍然活跃（分身跑完即回收 ⇒ 常见"选择还在、分身没了"） */
  const selectedLive = Boolean(delegate) && liveNames.includes(delegate)
  const staleSelection = loaded && Boolean(delegate) && !selectedLive
  /** 实际委托目标：优先用户所选（且必须仍在活跃列表），否则回落到列表第一个 */
  const target = selectedLive ? delegate : (list[0]?.name ?? '')
  const hasTaskText = Boolean((task || defaultTask).trim())
  /** 就绪：有任务文本 + 未在跑（两条路径都能真委托：具名分身 / 临时分身） */
  const readyToDelegate = hasTaskText && !running
  /** 走哪条链路：有活跃分身则用**用户选的那个**；否则用临时分身（现建现用、跑完即回收）。
   *  【为什么不再"没有分身就禁用"】具名端点要求分身先存在，而分身跑完即回收 ⇒
   *  禁用等于把界面变成死路。临时分身端点与模型侧 delegate 工具同链路，无需先建分身。 */
  const useEphemeral = !target

  const runDelegate = async () => {
    const name = target
    const text = (task || defaultTask).trim()
    setResult(null)
    if (!text) {
      // 空目标时按钮本就是禁用的；这里再兜一层，且给出**明确原因**（此前是静默 return）
      setError('请先填写要委托的任务（①目标，≥8 字符且可判定）。')
      return
    }
    setRunning(true)
    setError('')
    try {
      // 用原生 fetch 而非 hubPost：hubPost 在非 2xx 时只抛 "HTTP 409" 并丢弃响应体，
      // 而本接口的失败原因（error_code/error/channel）恰在响应体里，UI 必须看到。
      // 目标：走**真委派**端点（/execute 是容器占位骨架，只回占位文案 ⇒ "没跑通"）
      // 两条链路：有活跃分身 → 具名端点用它；否则 → 临时分身端点（与模型侧 delegate 同链路）
      const url = name
        ? `/api/subagent/${encodeURIComponent(name)}/delegate`
        : EPHEMERAL_DELEGATE_URL
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({
          task: text,
          ...(splitList(constraints).length ? { constraints: splitList(constraints) } : {}),
          ...(splitList(prohibitions).length ? { prohibitions: splitList(prohibitions) } : {}),
          ...(artifactFormat.trim() ? { artifact_format: artifactFormat.trim() } : {}),
          ...(budgetTokens.trim() ? { budget_tokens: Number(budgetTokens) } : {}),
          ...(timeoutSeconds.trim() ? { timeout_seconds: Number(timeoutSeconds) } : {}),
        }),
      })
      const body = (await res.json().catch(() => ({}))) as DelegateResult
      // 临时分身没有"用户选的名字"：用后端给的可读名（"临时分身"）回显，而不是空串
      setResult({ ...body, name: body?.name || name || '临时分身' })
      if (body?.ok) setTask('')
      // 成功与否都刷新委派记录：让"刚刚这一下"立刻出现在历史里（这是本面板存在的意义）
      await loadHistory()
    } catch (e) {
      setError(`委托执行失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="子代理：展示并选择当前任务委托的子代理"
        className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
          open
            ? 'border-emerald-600/60 bg-emerald-500/10 text-emerald-300'
            : 'border-slate-700 text-slate-400 hover:border-emerald-600/60 hover:bg-slate-800 hover:text-emerald-300'
        }`}
      >
        <Bot size={11} />
        子代理
        <span className="max-w-[110px] truncate text-slate-500">
          {/* 只回显**用户所选**（不是回落目标）：否则触发器上的名字会与列表项同名，
              既误导"这是我选的"，也让人和列表项分不清 */}
          {delegate || (list.length ? `${list.length} 个` : (recordsTotal ? `记录 ${recordsTotal}` : '未选择'))}
        </span>
        {list.length > 0 ? (
          <span className="rounded-full bg-slate-800 px-1.5 text-[9px] text-slate-400" title="活跃分身数">
            {list.length}
          </span>
        ) : recordsTotal ? (
          // 没有活跃分身时也给出可见线索：否则用户只会看到"未选择"，以为整个功能是死的
          <span className="rounded-full bg-slate-800 px-1.5 text-[9px] text-slate-500" title="历史委派记录数">
            {recordsTotal}
          </span>
        ) : null}
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-[70] w-[380px] rounded-xl border border-slate-700 bg-slate-900/98 p-3 text-[11.5px] shadow-2xl backdrop-blur">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-medium text-slate-200">🤖 当前任务委托的子代理</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void load()}
                disabled={loading}
                title="刷新子代理列表"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭子代理菜单"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                <X size={12} />
              </button>
            </div>
          </div>

          <p className="mb-2 text-[10.5px] leading-relaxed text-slate-500">
            选中的子代理即「当前任务委托对象」：委托执行时任务会下发给它，按钮上会回显所选名称。
          </p>

          {error && <p className="mb-2 rounded-md border border-rose-900/60 bg-rose-500/10 px-2 py-1 text-rose-300">{error}</p>}

          <div className="mb-2 max-h-56 overflow-y-auto">
            {list.length === 0 && !loading ? (
              <div className="rounded-md border border-slate-800 bg-slate-950/60 px-3 py-4 text-center text-slate-500">
                暂无活跃子代理
                <div className="mt-1 text-[10px] text-slate-600">
                  委派跑完即回收，所以这里常年为空属正常；发生过什么见下方「委派记录」。
                </div>
                <div className="mt-0.5 text-[10px] text-slate-600">
                  不选也能委托：没有活跃分身时走**临时分身**（现建现用、跑完即回收）。
                </div>
                <div className="mt-0.5 text-[10px] text-slate-600">
                  要固定用一个分身，请到「装配车间 → 分身创建与组装」创建后再回来选择。
                </div>
              </div>
            ) : (
              list.map((sa) => {
                const st = STATUS_TEXT(sa)
                const active = sa.name === delegate
                return (
                  <button
                    key={sa.name}
                    type="button"
                    onClick={() => pick(sa.name)}
                    className={`mb-1 flex w-full items-start gap-2 rounded-lg border px-2 py-1.5 text-left transition-colors ${
                      active
                        ? 'border-emerald-600/60 bg-emerald-500/10'
                        : 'border-slate-800 hover:bg-slate-800/60'
                    }`}
                  >
                    <span className="mt-[1px] w-3 shrink-0 text-emerald-400">
                      {active ? <Check size={12} /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <b className="truncate font-medium text-slate-200">{sa.name}</b>
                        <em className={`not-italic text-[10px] ${st.cls}`}>{st.text}</em>
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
                        {sa.model_id || '-'} · {sa.memory_provider || '-'} · 上下文{' '}
                        {sa.context_used ?? 0}/{sa.context_window ?? 0}
                      </span>
                      {(sa.tags ?? []).length > 0 && (
                        <span className="mt-0.5 block truncate text-[10px] text-slate-600">
                          标签：{(sa.tags ?? []).join('、')}
                        </span>
                      )}
                    </span>
                  </button>
                )
              })
            )}
          </div>

          {/* 委托执行（真委派：八要素 + 隔离执行 + Trace + 成本） */}
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
              <UserPlus size={10} /> 委托任务给 {target || '（无可选分身）'}
            </div>

            {/* 陈旧选择：localStorage 里的名字随分身回收而消失 —— 显式说清并给出清除入口，
                否则它的表现是"点了委托 → 404 分身不存在"，看着像功能坏了 */}
            {staleSelection && (
              <p className="mb-1.5 flex items-start gap-1.5 rounded-md border border-amber-900/60 bg-amber-500/10 px-2 py-1 text-[10.5px] leading-relaxed text-amber-300">
                <span className="min-w-0 flex-1">
                  已保存的选择「{delegate}」已不在活跃列表（分身跑完即回收或被销毁）。
                </span>
                <button
                  type="button"
                  onClick={clearDelegate}
                  className="shrink-0 rounded border border-amber-700/60 px-1.5 text-[10px] hover:bg-amber-500/10"
                >
                  清除选择
                </button>
              </p>
            )}

            {/* 执行通道不可用 → 真委派必然失败：提前说清楚，而不是让用户白点 */}
            {channel && channel.ok === false && (
              <p className="mb-1.5 rounded-md border border-amber-900/60 bg-amber-500/10 px-2 py-1 text-[10.5px] leading-relaxed text-amber-300">
                未配置执行通道（_llm 为空且 CP_SUBAGENT_AGENT_CLI 未设置）——真委派需要 LLM 或外部 agent CLI，
                当前点击会返回 E_DELEGATION_NO_CHANNEL。
              </p>
            )}

            <textarea
              className="mb-1.5 h-14 w-full resize-none rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-none focus:border-cyan-600"
              placeholder={defaultTask ? '留空则使用当前会话最近一条用户消息' : '输入要委托的任务（①目标，≥8 字符）…'}
              value={task}
              onChange={(e) => setTask(e.target.value)}
            />

            {/* 八要素中的可选项：留空由后端按文档缺省补齐 */}
            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              className="mb-1.5 flex w-full items-center gap-1 text-[10px] text-slate-500 hover:text-slate-300"
            >
              {advanced ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              高级：约束 / 禁止事项 / 产物格式 / 预算 / 超时（八要素，留空用缺省）
            </button>
            {advanced && (
              <div className="mb-1.5 space-y-1.5 rounded-md border border-slate-800 bg-slate-950/70 p-2">
                <label className="block text-[10px] text-slate-500">
                  ②约束（逗号或换行分隔；缺省「只读为主，不得修改仓库文件」）
                  <input
                    className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10.5px] text-slate-200 outline-none focus:border-cyan-600"
                    value={constraints}
                    onChange={(e) => setConstraints(e.target.value)}
                    placeholder="例如：只读仓库，不得修改任何文件"
                  />
                </label>
                <label className="block text-[10px] text-slate-500">
                  ④禁止事项（缺省「不得对外发送数据」；可留空列表）
                  <input
                    className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10.5px] text-slate-200 outline-none focus:border-cyan-600"
                    value={prohibitions}
                    onChange={(e) => setProhibitions(e.target.value)}
                    placeholder="例如：不得删除任何文件, 不得访问网络"
                  />
                </label>
                <div className="flex gap-1.5">
                  <label className="flex-1 text-[10px] text-slate-500">
                    ⑤产物格式
                    <input
                      className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10.5px] text-slate-200 outline-none focus:border-cyan-600"
                      value={artifactFormat}
                      onChange={(e) => setArtifactFormat(e.target.value)}
                      placeholder="文本要点"
                    />
                  </label>
                  <label className="w-20 text-[10px] text-slate-500">
                    ⑥预算 tok
                    <input
                      className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10.5px] text-slate-200 outline-none focus:border-cyan-600"
                      value={budgetTokens}
                      onChange={(e) => setBudgetTokens(e.target.value)}
                      placeholder="4000"
                    />
                  </label>
                  <label className="w-16 text-[10px] text-slate-500">
                    ⑦超时 s
                    <input
                      className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 text-[10.5px] text-slate-200 outline-none focus:border-cyan-600"
                      value={timeoutSeconds}
                      onChange={(e) => setTimeoutSeconds(e.target.value)}
                      placeholder="120"
                    />
                  </label>
                </div>
              </div>
            )}

            <button
              type="button"
              onClick={() => void runDelegate()}
              disabled={running || !hasTaskText}
              title={
                !hasTaskText
                  ? '请先填写要委托的任务（①目标）'
                  : useEphemeral
                    ? '当前没有活跃分身：将用**临时分身**执行（现建现用、跑完即回收），与模型侧 delegate 工具同一条链路'
                    : '把任务交给所选分身执行（真委派：八要素 + 隔离 + Trace + 成本）'
              }
              className={`flex w-full items-center justify-center gap-1.5 rounded-md border px-2 py-1 text-[11px] disabled:opacity-40 ${
                readyToDelegate
                  ? 'border-emerald-700/60 text-emerald-300 hover:bg-emerald-500/10'
                  : 'border-slate-700 text-slate-400'
              }`}
            >
              {running ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              {running
                ? '委托中…'
                : useEphemeral
                  ? '委托执行（临时分身）'
                  : '委托执行（用所选分身）'}
            </button>
          </div>

          {result && (
            <div
              className={`mt-2 rounded-lg border p-2 ${
                result.ok ? 'border-emerald-900/60 bg-emerald-500/5' : 'border-rose-900/60 bg-rose-500/5'
              }`}
            >
              <div className="mb-1 flex items-center justify-between text-[10px] text-slate-500">
                <span>
                  结果来源：{result.name}
                  {result.duration_ms != null && ` · ${Math.round(result.duration_ms)}ms`}
                  {result.tier && ` · tier=${result.tier}`}
                </span>
                <span className={result.ok ? 'text-emerald-400' : 'text-rose-400'}>
                  {result.ok ? '成功' : result.error_code || '失败'}
                </span>
              </div>
              {result.error && (
                <p className="mb-1 text-[10.5px] leading-relaxed text-rose-300">
                  {result.error}
                  {result.sub_reason ? `（${result.sub_reason}）` : ''}
                </p>
              )}
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] text-slate-300">
                {result.result || (result.ok ? '（无输出）' : '（未产出交付物）')}
              </pre>
              {result.trace_id && (
                <div className="mt-1 font-mono text-[9.5px] text-slate-600">
                  trace {result.trace_id}
                  {result.delegation_id ? ` · ${result.delegation_id}` : ''}
                  {result.artifact_count ? ` · 产物 ${result.artifact_count} 件` : ''}
                </div>
              )}
            </div>
          )}

          {/* 委派记录：回答"发生过什么"。活跃分身列表是"当前存活"的快照，
              在委派跑完即回收的现实下它常年为空 —— 这里是唯一能证明业务发生过的证据 */}
          <div className="mt-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
              <span>
                📜 委派记录
                {recordsTotal != null ? ` · 共 ${recordsTotal} 条` : ''}
                {records.length > 0 && recordsTotal != null && recordsTotal > records.length
                  ? `（显示最近 ${records.length} 条）`
                  : ''}
              </span>
              <button
                type="button"
                onClick={() => void loadHistory()}
                title="刷新委派记录"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                <RefreshCw size={10} />
              </button>
            </div>

            {recordsError && (
              <p className="mb-1 rounded-md border border-amber-900/60 bg-amber-500/10 px-2 py-1 text-[10.5px] text-amber-300">
                {recordsError}
              </p>
            )}

            {records.length === 0 ? (
              <p className="px-1 py-2 text-center text-[10.5px] text-slate-500">
                暂无委派记录
                <span className="mt-0.5 block text-[10px] text-slate-600">
                  模型工具 / 界面 / 批量委派，每发生一次都会落一条
                </span>
              </p>
            ) : (
              <div className="max-h-40 overflow-y-auto">
                {records.map((r, i) => (
                  <div
                    key={r.delegation_id || `${r.created_at || '?'}-${i}`}
                    className="mb-1 rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1"
                  >
                    <div className="flex items-center gap-1.5">
                      <em
                        className={`not-italic rounded-full border px-1.5 text-[9.5px] ${
                          r.ok ? 'border-emerald-700/60 text-emerald-300' : 'border-rose-800/60 text-rose-300'
                        }`}
                      >
                        {r.ok ? '成功' : '失败'}
                      </em>
                      <b className="min-w-0 flex-1 truncate font-mono text-[10.5px] font-normal text-slate-300">
                        {r.subagent || '（临时分身）'}
                      </b>
                      <span className="shrink-0 text-[9.5px] text-slate-500">{sourceLabel(r.source)}</span>
                    </div>
                    <div className="mt-0.5 truncate text-[10px] text-slate-400" title={r.goal || ''}>
                      {r.goal || '（无目标）'}
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 text-[9.5px] text-slate-600">
                      <span>{fmtRecordTime(r.created_at)}</span>
                      {r.duration_ms != null && <span>{Math.round(r.duration_ms)}ms</span>}
                      {r.tier && <span>tier={r.tier}</span>}
                      {!!r.artifact_count && <span>产物 {r.artifact_count} 件</span>}
                      {r.goal && (
                        <button
                          type="button"
                          onClick={() => setTask(r.goal || '')}
                          title="把这条记录的目标回填到输入框"
                          className="ml-auto shrink-0 rounded border border-slate-700 px-1 text-[9.5px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                        >
                          复用目标
                        </button>
                      )}
                    </div>
                    {r.error && (
                      <div className="mt-0.5 break-words text-[9.5px] leading-relaxed text-rose-400">
                        {r.error_code ? `${r.error_code} · ` : ''}
                        {r.error}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
