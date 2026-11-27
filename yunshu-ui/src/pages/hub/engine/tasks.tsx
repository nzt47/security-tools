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
import { Button, CodeBlock, ErrorBox, hubGet, hubPost, Notice, SectionTitle, Select } from '../components/ui'
import {
  ASYNC_TASKS,
  BACKGROUND_TASKS,
  asyncTask,
  asyncTaskCancel,
  backgroundTask,
  backgroundTaskCancel,
  backgroundTaskResult,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-caption text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'

const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

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

/** E4-2：脚本编排 run 的只读投影（后端 /api/background/tasks 的 orchestration 块） */
interface OrcRow {
  run_id?: string
  ts?: string
  line?: string
  ok?: boolean
  code?: string
  duration_ms?: number
  calls?: number
  spawned?: number
  denied?: number
  whitelist_size?: number
}

export default function TaskQueue() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [statusFilter, setStatusFilter] = useState('')
  const [bgRows, setBgRows] = useState<TaskRow[]>([])
  const [bgMeta, setBgMeta] = useState<Record<string, unknown> | null>(null)
  const [asyncRows, setAsyncRows] = useState<TaskRow[]>([])
  const [orcRows, setOrcRows] = useState<OrcRow[]>([])
  const [orcTotal, setOrcTotal] = useState(0)

  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [detailLabel, setDetailLabel] = useState('')
  const [confirmCancel, setConfirmCancel] = useState('')

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async (status = statusFilter) => {
    setBusy('load'); setErr('')
    try {
      const [bg, at] = await Promise.allSettled([
        hubGet<{
          tasks?: TaskRow[]
          total?: number
          running?: number
          active?: number
          ok?: boolean
          orchestration?: { total?: number; runs?: OrcRow[] }
        }>(
          BACKGROUND_TASKS + '?limit=50' + (status ? '&status=' + encodeURIComponent(status) : ''),
        ),
        hubGet<{ tasks?: TaskRow[]; total?: number; ok?: boolean }>(ASYNC_TASKS + '?limit=50'),
      ])
      if (bg.status === 'fulfilled') {
        setBgRows(bg.value?.tasks ?? [])
        setBgMeta({ total: bg.value?.total ?? 0, running: bg.value?.running ?? 0, active: bg.value?.active ?? 0 })
        setOrcRows(bg.value?.orchestration?.runs ?? [])
        setOrcTotal(bg.value?.orchestration?.total ?? 0)
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
          <div key={key} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-caption">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-micro text-cyan-300">{id}</span>
              <span className="text-slate-300">{String(r.name ?? '')}</span>
              <span className={'text-micro ' + (status === 'completed' || status === 'success' ? 'text-emerald-400' : status === 'failed' || status === 'error' ? 'text-red-400' : 'text-amber-400')}>
                {status || '—'}
              </span>
              <span className="font-mono text-micro text-slate-600">{String(r.created_at ?? '')}</span>
              <span className="ml-auto flex flex-wrap items-center gap-1">
                <Button type="button" variant="default" size="xs" onClick={() => void openDetail(kind, id)} disabled={loading} aria-label={'任务详情 ' + id}>
                  <FileText size={11} /> 详情
                </Button>
                {kind === 'bg' && (
                  <Button type="button" variant="default" size="xs" onClick={() => void openResult(id)} disabled={loading} aria-label={'任务结果 ' + id}>
                    结果
                  </Button>
                )}
                <Button
                  type="button"
                  variant="danger-outline" size="xs"
                  onClick={() => setConfirmCancel(isOpen ? '' : key)}
                  disabled={loading || !canCancel}
                  aria-label={'取消任务 ' + id}
                >
                  <Ban size={11} /> 取消
                </Button>
              </span>
            </div>
            {isOpen && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded-sm border border-red-900/50 bg-red-500/5 px-2 py-1.5">
                <span className="text-micro text-red-300">
                  取消 {id}（当前 {status || '未知'}）可能留下半成品。确定？
                </span>
                <Button type="button" variant="danger-outline" size="xs" onClick={() => void cancel(kind, id)} disabled={loading} aria-label={'确认取消 ' + id}>
                  {busy === 'cancel:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认取消
                </Button>
                <Button type="button" variant="default" size="xs" onClick={() => setConfirmCancel('')}>返回</Button>
              </div>
            )}
          </div>
        )
      })}
      {rows.length === 0 && <div className="text-caption text-slate-500">没有任务。</div>}
    </div>
  )

  return (
    <div className="h-full overflow-y-auto p-4">
      <SectionTitle
        icon={<ListChecks size={14} className="text-cyan-400" />}
        title="任务队列"
        description="后台任务（DAG）与异步任务表 —— 同一个问题的两代实现"
        actions={(
          <>
            <Select value={statusFilter} onChange={(v) => { setStatusFilter(v); void load(v)}} aria-label="后台任务状态过滤" size="sm" placeholder="全部状态" placeholderOptionDisabled={false} options={[{ label: 'pending', value: 'pending' }, { label: 'running', value: 'running' }, { label: 'completed', value: 'completed' }, { label: 'failed', value: 'failed' }]} />
            <Button type="button" variant="default" size="xs" onClick={() => void load()} disabled={loading} aria-label="刷新任务队列">
              {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
            </Button>
          </>
        )}
      />

      {err && <ErrorBox message={err} />}
      {msg && (
        <Notice tone="success" size="sm" icon={<CheckCircle2 size={11} />} className="mb-2 px-2 py-1">
          {msg}
        </Notice>
      )}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['后台任务', bgRows.length],
          ['运行中', (bgMeta?.running as number) ?? 0],
          ['异步任务', asyncRows.length],
          ['编排 run', orcTotal],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-micro text-slate-500">{String(k)}</div>
            <div className="font-mono text-body-lg text-cyan-300">{String(v)}</div>
          </div>
        ))}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
          <Clock size={11} className="text-cyan-400" /> 后台任务（{bgRows.length}）
        </div>
        {renderRows(bgRows, 'bg')}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
          <ListChecks size={11} className="text-cyan-400" /> 异步任务（{asyncRows.length}）
        </div>
        {renderRows(asyncRows, 'async')}
      </div>

      {/* E4-2：脚本编排 run 的只读投影。同步工具、跑完即定，所以这一组**没有取消**按钮；
          数据源与后台任务同一个 GET（orchestration 块），不额外加路由。 */}
      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
          <ListChecks size={11} className="text-cyan-400" /> 脚本编排 run（{orcTotal}）
        </div>
        <div className="flex flex-col gap-1">
          {orcRows.map((r) => {
            const id = String(r.run_id ?? '')
            const ok = r.ok === true
            return (
              <div key={'orc:' + id} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-caption">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-micro text-cyan-300">{id || '—'}</span>
                  <span className="text-slate-300">{r.line || '默认主线'}</span>
                  <span className={'text-micro ' + (ok ? 'text-emerald-400' : 'text-red-400')}>
                    {ok ? 'ok' : (r.code || 'failed')}
                  </span>
                  <span className="font-mono text-micro text-slate-500">
                    {String(r.calls ?? 0)} 次调用 / {String(r.duration_ms ?? 0)}ms / 白名单 {String(r.whitelist_size ?? 0)}
                  </span>
                  <span className="ml-auto font-mono text-micro text-slate-600">{String(r.ts ?? '')}</span>
                </div>
              </div>
            )
          })}
          {orcRows.length === 0 && <div className="text-caption text-slate-500">还没有脚本编排 run（默认关闭 CP_ORCH_SCRIPT_ENABLED=0）。</div>}
        </div>
      </div>

      {detail && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-caption text-slate-400">
            <FileText size={11} className="text-cyan-400" /> {detailLabel}
            <Button type="button" onClick={() => setDetail(null)} variant="default" size="xs" className="ml-auto">关闭</Button>
          </div>
          <CodeBlock maxH={288}>{JSON.stringify(detail, null, 2)}</CodeBlock>
        </div>
      )}
    </div>
  )
}
