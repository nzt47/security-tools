/**
 * BackgroundTasksMenu —— 会话任务区「后台任务」下拉
 * ------------------------------------------------------------------
 * 需求：添加显示后台任务的下拉菜单按钮，用于查看和管理系统后台运行的任务。
 *
 * 数据源（本仓库新增 HTTP 面）：/api/background/tasks*
 *   GET  /api/background/tasks                 列表（状态 / 进度 / 创建与完成时间）
 *        —— 另有 history: {total, records, path}：**历史提交摘要**，见下
 *   POST /api/background/tasks/<id>/cancel     取消（仅 pending/running）
 * 后端执行器：agent.async_executor.AsyncExecutor（SingletonManager 单例），
 * 覆盖工具异步任务（如过程蒸馏 process_distill_run）等后台作业。
 *
 * 【为什么要有 history 摘要 —— 线上反馈"永远看不到后台任务"的一半原因】
 * 列表是**内存态**：完成的任务超过 result_ttl（缺省 1 小时）被清理、进程重启即清空。
 * 于是"跑过任务"这件事在界面上会彻底消失，用户只看到"暂无后台任务"，
 * 无从分辨"确实没跑过"与"跑过但记录被清理了"。执行器另有 data/async_tasks.jsonl
 * 追加写（此前**只写不读**），history 摘要把它读出来回答这个问题。
 *
 * 行为：展开即加载并每 5s 轮询（面板关闭时停止）；按钮上显示运行中数量角标。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Ban, ChevronRight, Loader2, RefreshCw, X, Zap } from 'lucide-react'
import { hubGet, hubPost } from '../../../pages/hub/components/ui'

const POLL_MS = 5000

interface BgTask {
  id: string
  name?: string
  tool_name?: string
  status: string
  progress?: string
  created_at?: string
  started_at?: string
  completed_at?: string
  error?: string
  timeout?: number | null
  has_result?: boolean
}

/** 历史提交记录（响应里的 history.records；与列表同一种摘要形状，不含结果本体） */
type HistoryRecord = BgTask

/** GET /api/background/tasks 的响应（history 为空态补充信息，后端随列表一次返回） */
interface BgListResponse {
  ok?: boolean
  tasks?: BgTask[]
  total?: number
  active?: number
  error?: string
  /** total 为 null = 历史文件过大未统计（"未统计"不等于 0，展示上要区分开） */
  history?: { total?: number | null; records?: HistoryRecord[]; path?: string }
}

const STATUS_STYLE: Record<string, { label: string; cls: string }> = {
  pending: { label: '排队中', cls: 'border-slate-600 text-slate-300' },
  running: { label: '运行中', cls: 'border-cyan-600/60 text-cyan-300' },
  completed: { label: '已完成', cls: 'border-emerald-600/60 text-emerald-300' },
  failed: { label: '失败', cls: 'border-rose-700/60 text-rose-300' },
  cancelled: { label: '已取消', cls: 'border-amber-700/60 text-amber-300' },
}

const fmtTime = (s?: string) => (s ? s.replace('T', ' ').slice(5) : '')
const isActive = (t: BgTask) => t.status === 'pending' || t.status === 'running'

export function BackgroundTasksMenu() {
  const [open, setOpen] = useState(false)
  const [tasks, setTasks] = useState<BgTask[]>([])
  const [total, setTotal] = useState(0)
  const [active, setActive] = useState(0)
  /** 历史提交（执行器落盘的记录；用于回答"到底跑没跑过"） */
  const [history, setHistory] = useState<HistoryRecord[]>([])
  const [historyTotal, setHistoryTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await hubGet<BgListResponse>('/api/background/tasks?limit=50')
      if (r?.ok === false) {
        setError(String(r.error ?? '后台任务查询失败'))
      } else {
        setTasks(Array.isArray(r?.tasks) ? r.tasks : [])
        setTotal(Number(r?.total ?? 0))
        setActive(Number(r?.active ?? 0))
        // 历史摘要与内存任务在同一次响应里回来（不额外发请求：只读控件不该变成流量源）
        setHistory(Array.isArray(r?.history?.records) ? r.history.records : [])
        setHistoryTotal(typeof r?.history?.total === 'number' ? r.history.total : null)
        setError('')
      }
    } catch (e) {
      setError(`后台任务不可用：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  // 展开时加载 + 轮询（关闭立即停止，避免后台常驻请求）
  useEffect(() => {
    if (!open) return
    void load()
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [open, load])

  // 未展开时也低频探测一次运行中数量（角标可见），随后每 30s 刷新
  useEffect(() => {
    if (open) return
    void load()
    const timer = setInterval(() => void load(), 30_000)
    return () => clearInterval(timer)
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

  const cancel = async (id: string) => {
    setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(`/api/background/tasks/${encodeURIComponent(id)}/cancel`)
      if (r?.ok === false) setError(String(r.error ?? '取消失败'))
      else setMsg(`已取消任务 ${id}`)
      await load()
    } catch (e) {
      setError(`取消失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="后台任务：查看/管理系统中后台运行的任务"
        className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
          open
            ? 'border-violet-600/60 bg-violet-500/10 text-violet-300'
            : 'border-slate-700 text-slate-400 hover:border-violet-600/60 hover:bg-slate-800 hover:text-violet-300'
        }`}
      >
        <Zap size={11} />
        后台任务
        {active > 0 && (
          <span className="rounded-full bg-cyan-500/20 px-1.5 text-[9px] font-medium text-cyan-300" title="运行中任务数">
            {active}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-[70] w-[420px] rounded-xl border border-slate-700 bg-slate-900/98 p-3 text-[11.5px] shadow-2xl backdrop-blur">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-medium text-slate-200">
              ⚡ 后台任务
              <span className="ml-1.5 text-[10px] font-normal text-slate-500">
                共 {total} · 运行中 {active} · 每 5s 刷新
                {historyTotal ? ` · 历史 ${historyTotal}` : ''}
              </span>
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void load()}
                disabled={loading}
                title="立即刷新"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭后台任务菜单"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                <X size={12} />
              </button>
            </div>
          </div>

          {error && <p className="mb-2 rounded-md border border-rose-900/60 bg-rose-500/10 px-2 py-1 text-rose-300">{error}</p>}
          {msg && <p className="mb-2 rounded-md border border-emerald-900/60 bg-emerald-500/10 px-2 py-1 text-emerald-300">{msg}</p>}

          <div className="max-h-72 overflow-y-auto">
            {tasks.length === 0 ? (
              <div className="rounded-md border border-slate-800 bg-slate-950/60 px-3 py-4 text-center text-slate-500">
                {loading ? '加载中…' : '暂无后台任务'}
                <div className="mt-1 text-[10px] text-slate-600">
                  长耗时工具（process_distill_run / submit_task 等）会在此出现
                </div>
                {/* 空态的**关键补充**：内存里没有 ≠ 从来没跑过。历史读的是执行器落盘的 JSONL，
                    所以"跑过但已被 TTL 清理/进程重启"也能在这里看见 */}
                {history.length > 0 && (
                  <div className="mt-2 border-t border-slate-800 pt-2 text-left">
                    <div className="mb-1 text-[10px] leading-relaxed text-slate-500">
                      历史提交{historyTotal != null ? ` ${historyTotal} 条` : '（条数未统计）'}
                      <span className="block text-slate-600">
                        已完成的任务保留 1 小时后从内存清理，故"当前为空"不代表"从来没跑过"
                      </span>
                    </div>
                    {history.map((h, i) => {
                      const st = STATUS_STYLE[h.status] ?? { label: h.status, cls: 'border-slate-600 text-slate-300' }
                      return (
                        <div
                          key={h.id || `${h.created_at || '?'}-${i}`}
                          className="flex items-center gap-1.5 py-0.5 font-mono text-[10px] text-slate-500"
                        >
                          <span className="shrink-0 text-slate-600">{fmtTime(h.created_at) || '-'}</span>
                          <span className="min-w-0 flex-1 truncate text-slate-400" title={h.error || h.tool_name || ''}>
                            {h.name || h.tool_name || h.id}
                          </span>
                          <em className={`shrink-0 not-italic rounded-full border px-1.5 text-[9px] ${st.cls}`}>
                            {st.label}
                          </em>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            ) : (
              tasks.map((t) => {
                const st = STATUS_STYLE[t.status] ?? { label: t.status, cls: 'border-slate-600 text-slate-300' }
                const shown = expanded === t.id
                return (
                  <div key={t.id} className="mb-1 rounded-lg border border-slate-800 bg-slate-950/50">
                    <div className="flex items-center gap-2 px-2 py-1.5">
                      <button
                        type="button"
                        onClick={() => setExpanded(shown ? null : t.id)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                        title="展开/收起任务详情"
                      >
                        <ChevronRight size={11} className={`shrink-0 text-slate-500 transition-transform ${shown ? 'rotate-90' : ''}`} />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1.5">
                            <b className="truncate font-medium text-slate-200">{t.name || t.tool_name || t.id}</b>
                            <em className={`not-italic rounded-full border px-1.5 text-[9.5px] ${st.cls}`}>{st.label}</em>
                          </span>
                          <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
                            {t.tool_name || '-'} · {t.id}
                            {t.progress ? ` · ${t.progress}` : ''}
                          </span>
                        </span>
                      </button>
                      {isActive(t) && (
                        <button
                          type="button"
                          onClick={() => void cancel(t.id)}
                          title="取消该后台任务（仅排队/运行中可取消）"
                          className="flex shrink-0 items-center gap-1 rounded-md border border-amber-700/60 px-1.5 py-0.5 text-[10px] text-amber-300 hover:bg-amber-500/10"
                        >
                          <Ban size={10} /> 取消
                        </button>
                      )}
                    </div>
                    {shown && (
                      <div className="border-t border-slate-800 px-2 py-1.5 font-mono text-[10.5px] text-slate-400">
                        <div>创建：{fmtTime(t.created_at) || '-'}</div>
                        <div>开始：{fmtTime(t.started_at) || '-'}</div>
                        <div>完成：{fmtTime(t.completed_at) || '-'}</div>
                        {t.timeout != null && <div>超时：{t.timeout}s</div>}
                        {t.has_result && <div className="text-emerald-400">已有结果（可在任务结果接口查看）</div>}
                        {t.error && <div className="mt-1 whitespace-pre-wrap break-words text-rose-400">{t.error}</div>}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>
      )}
    </div>
  )
}
