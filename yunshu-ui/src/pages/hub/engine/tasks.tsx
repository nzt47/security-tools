/**
 * 任务队列 —— 后台任务 + 异步任务（7 条）
 * ------------------------------------------------------------------
 *   后台任务 /api/background/tasks       列表（状态过滤）
 *            /api/background/tasks/<id>  单任务状态
 *            /api/background/tasks/<id>/result  取结果（未完成不算错误）
 *            /api/background/tasks/<id>/cancel  取消（仅 pending/running）
 *   异步任务 /api/tasks、/api/tasks/<id>、/api/tasks/<id>/cancel
 *
 * 【为什么这两族并排而不是各占一个 Tab】它们是同一种东西的两代实现
 * （DAG 后台任务 / 异步任务表），用户问的都是"我交的活儿到哪了"。
 * 分开会让"我那个任务在哪个表里"变成一次考古。
 *
 * 【为什么取消要确认】任务是**有代价的在跑进程**：取消可能留下半成品。
 * 故取消前把 task_id 与当前状态摆出来，点确认才发。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Ban, CheckCircle2, Clock, FileText, Loader2, ListChecks, RefreshCw,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  ASYNC_TASKS,
  BACKGROUND_TASKS,
  asyncTask,
  asyncTaskCancel,
  backgroundTask,
  backgroundTaskCancel,
  backgroundTaskResult,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface TaskRow {
  task_id?: string
  id?: string
  name?: string
  status?: string
  created_at?: string
  updated_at?: string
  error?: string
  [k: string]: unknown
}

export default function TaskQueue() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [statusFilter, setStatusFilter] = useState('')
  const [bgRows, setBgRows] = useState<TaskRow[]>([])
  const [bgMeta, setBgMeta] = useState<Record<string, unknown> | null>(null)
  const [asyncRows, setAsyncRows] = useState<TaskRow[]>([])

  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [detailLabel, setDetailLabel] = useState('')
  const [confirmCancel, setConfirmCancel] = useState('')

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async (status = statusFilter) => {
    setBusy('load'); setErr('')
    try {
      const [bg, at] = await Promise.allSettled([
        hubGet<{ tasks?: TaskRow[]; total?: number; running?: number; active?: number; ok?: boolean }>(
          BACKGROUND_TASKS + '?limit=50' + (status ? '&status=' + encodeURIComponent(status) : ''),
        ),
        hubGet<{ tasks?: TaskRow[]; total?: number; ok?: boolean }>(ASYNC_TASKS + '?limit=50'),
      ])
      if (bg.status === 'fulfilled') {
        setBgRows(bg.value?.tasks ?? [])
        setBgMeta({ total: bg.value?.total ?? 0, running: bg.value?.running ?? 0, active: bg.value?.active ?? 0 })
      }
      if (at.status === 'fulfilled') setAsyncRows(at.value?.tasks ?? [])
    } catch (e) { fail('读取任务列表失败', e) } finally { setBusy('') }
  }, [statusFilter])

  useEffect(() => { void load() }, [load])

  const openDetail = async (kind: 'bg' | 'async', id: string) => {
    setBusy('detail'); setErr(''); setDetail(null)
    setDetailLabel(kind + ':' + id)
    try {
      const r = kind === 'bg'
        ? await hubGet<Record<string, unknown>>(backgroundTask(id))
        : await hubGet<Record<string, unknown>>(asyncTask(id))
      setDetail(r)
    } catch (e) { fail('读取任务详情失败', e) } finally { setBusy('') }
  }

  const openResult = async (id: string) => {
    setBusy('result'); setErr(''); setDetail(null)
    setDetailLabel('result:' + id)
    try {
      const r = await hubGet<Record<string, unknown>>(backgroundTaskResult(id))
      setDetail(r)
    } catch (e) { fail('读取任务结果失败', e) } finally { setBusy('') }
  }

  const cancel = async (kind: 'bg' | 'async', id: string) => {
    setBusy('cancel:' + id); setErr(''); setMsg('')
    try {
      const url = kind === 'bg' ? backgroundTaskCancel(id) : asyncTaskCancel(id)
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(url, {})
      if (r?.ok === false) { setErr('取消失败：' + (r.error || 'ok=false')); return }
      setMsg('已请求取消：' + id + (r?.message ? '（' + r.message + '）' : ''))
      setConfirmCancel('')
      await load()
    } catch (e) { fail('取消失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  const renderRows = (rows: TaskRow[], kind: 'bg' | 'async') => (
    <div className="flex flex-col gap-1">
      {rows.map((r) => {
        const id = (r.task_id ?? r.id ?? '') as string
        const key = kind + ':' + id
        const isOpen = confirmCancel === key
        const status = String(r.status ?? '')
        const canCancel = kind === 'bg' ? ['pending', 'running'].includes(status) : true
        return (
          <div key={key} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[11px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[10px] text-cyan-300">{id}</span>
              <span className="text-slate-300">{String(r.name ?? '')}</span>
              <span className={'text-[10px] ' + (status === 'completed' || status === 'success' ? 'text-emerald-400' : status === 'failed' || status === 'error' ? 'text-red-400' : 'text-amber-400')}>
                {status || '—'}
              </span>
              <span className="font-mono text-[10px] text-slate-600">{String(r.created_at ?? '')}</span>
              <span className="ml-auto flex flex-wrap items-center gap-1">
                <button type="button" className={BTN} onClick={() => void openDetail(kind, id)} disabled={loading} aria-label={'任务详情 ' + id}>
                  <FileText size={11} /> 详情
                </button>
                {kind === 'bg' && (
                  <button type="button" className={BTN} onClick={() => void openResult(id)} disabled={loading} aria-label={'任务结果 ' + id}>
                    结果
                  </button>
                )}
                <button
                  type="button"
                  className={BTN_RED}
                  onClick={() => setConfirmCancel(isOpen ? '' : key)}
                  disabled={loading || !canCancel}
                  aria-label={'取消任务 ' + id}
                >
                  <Ban size={11} /> 取消
                </button>
              </span>
            </div>
            {isOpen && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded border border-red-900/50 bg-red-500/5 px-2 py-1.5">
                <span className="text-[10px] text-red-300">
                  取消 {id}（当前 {status || '未知'}）可能留下半成品。确定？
                </span>
                <button type="button" className={BTN_RED} onClick={() => void cancel(kind, id)} disabled={loading} aria-label={'确认取消 ' + id}>
                  {busy === 'cancel:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认取消
                </button>
                <button type="button" className={BTN} onClick={() => setConfirmCancel('')}>返回</button>
              </div>
            )}
          </div>
        )
      })}
      {rows.length === 0 && <div className="text-[11px] text-slate-500">没有任务。</div>}
    </div>
  )

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <ListChecks size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">任务队列</h2>
        <span className="text-[11px] text-slate-500">后台任务（DAG）与异步任务表 —— 同一个问题的两代实现</span>
        <div className="ml-auto flex items-center gap-1.5">
          <select className={INPUT} value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); void load(e.target.value) }} aria-label="后台任务状态过滤">
            <option value="">全部状态</option>
            <option value="pending">pending</option>
            <option value="running">running</option>
            <option value="completed">completed</option>
            <option value="failed">failed</option>
          </select>
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label="刷新任务队列">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && (
        <div className="mb-2 flex items-center gap-1 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">
          <CheckCircle2 size={11} /> {msg}
        </div>
      )}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['后台任务', bgRows.length],
          ['运行中', (bgMeta?.running as number) ?? 0],
          ['活跃', (bgMeta?.active as number) ?? 0],
          ['异步任务', asyncRows.length],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="font-mono text-[13px] text-cyan-300">{String(v)}</div>
          </div>
        ))}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
          <Clock size={11} className="text-cyan-400" /> 后台任务（{bgRows.length}）
        </div>
        {renderRows(bgRows, 'bg')}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
          <ListChecks size={11} className="text-cyan-400" /> 异步任务（{asyncRows.length}）
        </div>
        {renderRows(asyncRows, 'async')}
      </div>

      {detail && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
            <FileText size={11} className="text-cyan-400" /> {detailLabel}
            <button type="button" className={BTN + ' ml-auto'} onClick={() => setDetail(null)}>关闭</button>
          </div>
          <pre className={PRE + ' max-h-72'}>{JSON.stringify(detail, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}
