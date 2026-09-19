/**
 * SubagentMenu —— 会话任务区「子代理」下拉
 * ------------------------------------------------------------------
 * 需求：在 UI 的会话任务区域添加子代理显示下拉菜单按钮，用于展示和选择
 * 当前任务委托的子代理。
 *
 * 数据源：GET /api/subagent/list（P4 分身生命周期；agent.orchestrator.SubagentManager）
 * 能力：
 *   - 展示全部分身（名称 / 模型 / 记忆提供方 / 上下文占用 / TTL 与存活时间）；
 *   - **选择**当前任务委托的子代理（LocalStorage 持久化，按钮上回显所选名称）；
 *   - 「委托执行」：把任务交给所选分身执行（POST /api/subagent/<name>/execute），
 *     任务内容默认预填当前会话最近一条用户消息（fail-soft，可编辑）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, Check, Loader2, Play, RefreshCw, UserPlus, X } from 'lucide-react'
import { hubGet, hubPost } from '../../../pages/hub/components/ui'

/** 当前任务委托的子代理（LocalStorage 持久化；跨会话保留用户选择） */
const DELEGATE_KEY = 'yunshu.subagent.delegate'

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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [delegate, setDelegate] = useState<string>(readDelegate)
  const [task, setTask] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<{ name: string; output: string; error?: string; ms?: number } | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await hubGet<{ ok?: boolean; subagents?: Subagent[]; count?: number; error?: string }>(
        '/api/subagent/list',
      )
      if (r?.ok === false) {
        setError(String(r.error ?? '分身列表查询失败'))
        setList([])
      } else {
        setList(Array.isArray(r?.subagents) ? r.subagents : [])
      }
    } catch (e) {
      // 分身系统未启用（subagent.enabled=False）时接口报错属正常，给出可执行提示
      setError(`子代理列表不可用：${e instanceof Error ? e.message : String(e)}（请确认后端已启动且 subagent.enabled=true）`)
      setList([])
    } finally {
      setLoading(false)
    }
  }, [])

  // 首次展开时加载；关闭后保留已加载数据
  useEffect(() => {
    if (open) void load()
  }, [open, load])

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

  const runDelegate = async () => {
    const name = delegate || list[0]?.name
    const text = (task || defaultTask).trim()
    if (!name || !text) return
    setRunning(true)
    setResult(null)
    setError('')
    try {
      const r = await hubPost<{ ok?: boolean; result?: { output?: string; error?: string; duration_ms?: number }; error?: string }>(
        `/api/subagent/${encodeURIComponent(name)}/execute`,
        { task: text },
      )
      if (r?.ok === false) {
        setError(String(r.error ?? '委托执行失败'))
      } else {
        setResult({
          name,
          output: String(r?.result?.output ?? '（无输出）'),
          error: r?.result?.error || undefined,
          ms: r?.result?.duration_ms,
        })
        setTask('')
      }
    } catch (e) {
      setError(`委托执行失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setRunning(false)
    }
  }

  const current = list.find((s) => s.name === delegate)

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
          {delegate || (list.length ? `${list.length} 个` : '未选择')}
        </span>
        {list.length > 0 && (
          <span className="rounded-full bg-slate-800 px-1.5 text-[9px] text-slate-400">{list.length}</span>
        )}
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
                  可在「装配车间 → 分身创建与组装」创建后回来选择
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

          {/* 委托执行 */}
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-1 text-[10px] uppercase tracking-wider text-slate-500">
              <UserPlus size={10} /> 委托任务给 {current?.name || delegate || '（未选择）'}
            </div>
            <textarea
              className="mb-1.5 h-14 w-full resize-none rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-none focus:border-cyan-600"
              placeholder={defaultTask ? '留空则使用当前会话最近一条用户消息' : '输入要委托的任务…'}
              value={task}
              onChange={(e) => setTask(e.target.value)}
            />
            <button
              type="button"
              onClick={() => void runDelegate()}
              disabled={running || (!delegate && list.length === 0) || !(task || defaultTask).trim()}
              className="flex w-full items-center justify-center gap-1.5 rounded-md border border-emerald-700/60 px-2 py-1 text-[11px] text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40"
            >
              {running ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
              {running ? '委托中…' : '委托执行'}
            </button>
          </div>

          {result && (
            <div className="mt-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2">
              <div className="mb-1 flex items-center justify-between text-[10px] text-slate-500">
                <span>
                  结果来源：{result.name}
                  {result.ms != null && ` · ${result.ms}ms`}
                </span>
                {result.error && <span className="text-rose-400">含错误</span>}
              </div>
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] text-slate-300">
                {result.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
