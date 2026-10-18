/**
 * 定时任务 —— 调度任务管理
 * 数据源：/api/schedules（列表/创建/暂停/恢复/删除）
 */
import { useEffect, useState } from 'react'
import { Plus, Trash2, Pause, Play, Zap, Loader2, History as HistoryIcon } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubDelete, hubGet, hubPost } from '../components/ui'

import {
  SCHEDULES,
  scheduleById,
  schedulePause,
  scheduleResume,
  SCHEDULER_OVERVIEW,
  SCHEDULER_CONFIGURE,
  SCHEDULER_EXECUTE_NOW,
  SCHEDULES_HISTORY,
} from '@/api/endpoints';
// 【P1-front 第三批】后端 GET /api/schedules 已带 X-Envelope: v2，改用显式信封解析。
// 同路径的 POST（创建任务）是另一个端点，仍是普通 JSON。
import { getEnvelope } from '@/api/envelope';

interface Task {
  id: string
  name: string
  action?: string
  interval_minutes?: number
  cron_expr?: string
  enabled?: boolean
  next_run?: string
  [k: string]: unknown
}

/** `GET /api/schedules` 的业务载荷（后端：`{ok, tasks, total}`）。 */
interface ScheduleList {
  ok?: boolean
  tasks?: Task[]
  total?: number
}

/** `GET /api/scheduler/overview` 的单条任务（调度器运行态）。 */
interface SchedTask {
  name: string
  task_id?: string
  type: string
  enabled: boolean
  last_run?: string | null
  schedule_text: string
  kind: 'cron' | 'interval' | 'command'
  hour?: number | null
  minute?: number | null
  zero_token?: boolean | null
  owner?: string
  editable_time?: boolean
}

export default function EngineScheduler() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [sched, setSched] = useState<SchedTask[]>([])
  const [schedMsg, setSchedMsg] = useState('')
  /** 正在"立即执行"的任务 id（同一时刻只允许一个，避免重复触发同一条任务） */
  const [busyRun, setBusyRun] = useState('')
  /** 另一族调度器（agent.scheduling）的执行历史：不自动拉，点开才拉 */
  const [histOpen, setHistOpen] = useState(false)
  const [histRows, setHistRows] = useState<Record<string, unknown>[]>([])
  const [histBusy, setHistBusy] = useState(false)
  const [editName, setEditName] = useState('')
  const [editHour, setEditHour] = useState('')
  const [editMinute, setEditMinute] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [action, setAction] = useState('')
  const [interval, setInterval] = useState('30')
  const [cron, setCron] = useState('')

  const loadSched = () => {
    getEnvelope<{ tasks?: SchedTask[] }>(SCHEDULER_OVERVIEW).then((d) => {
      setSched(d?.tasks ?? [])
    }).catch((e) => { setSchedMsg(String(e)) })
  }

  const load = () => {
    setLoading(true)
    getEnvelope<ScheduleList>(SCHEDULES).then((d) => {
      setTasks(d?.tasks ?? [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
    loadSched()
  }

  useEffect(load, [])

  const create = async () => {
    try {
      await hubPost(SCHEDULES, {
        name, action, interval_minutes: Number(interval) || 0, cron_expr: cron,
      })
      setShowForm(false)
      setName(''); setAction(''); setCron('')
      load()
    } catch (e) { setError(String(e)) }
  }

  const pause = async (id: string) => {
    try { await hubPost(schedulePause(id)); load() } catch (e) { setError(String(e)) }
  }
  const resume = async (id: string) => {
    try { await hubPost(scheduleResume(id)); load() } catch (e) { setError(String(e)) }
  }
  const remove = async (id: string) => {
    try { await hubDelete(scheduleById(id)); load() } catch (e) { setError(String(e)) }
  }

  const toggleSched = async (t: SchedTask) => {
    try {
      await hubPost(SCHEDULER_CONFIGURE, { name: t.name, enabled: !t.enabled })
      setSchedMsg(`${t.name}：已${t.enabled ? '暂停' : '恢复'}`)
      loadSched()
    } catch (e) { setSchedMsg(String(e)) }
  }

  /** 立即执行（id 基，agent.task_scheduler）：只触发一次，不改调度计划。 */
  const runNow = async (t: SchedTask) => {
    if (!t.task_id) { setSchedMsg('该任务没有 task_id，无法立即执行'); return }
    setBusyRun(t.task_id)
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; result?: unknown }>(SCHEDULER_EXECUTE_NOW, { id: t.task_id })
      setSchedMsg(r?.ok === false
        ? ('立即执行失败：' + (r.error || 'ok=false'))
        : (t.name + '：已立即执行一次（未改动调度计划）'))
      loadSched()
    } catch (e) { setSchedMsg(String(e)) } finally { setBusyRun('') }
  }

  /** 另一族调度器（/api/schedules）的执行历史：点开才拉，避免每次进页面都打一遍。 */
  const loadHist = async () => {
    setHistBusy(true)
    try {
      const r = await hubGet<{ history?: Record<string, unknown>[] }>(SCHEDULES_HISTORY + '?limit=20')
      setHistRows(r?.history ?? [])
    } catch (e) { setSchedMsg(String(e)) } finally { setHistBusy(false) }
  }

  const pickTask = (name: string) => {
    setEditName(name)
    const t = sched.find((x) => x.name === name)
    setEditHour(t?.hour == null ? '' : String(t.hour))
    setEditMinute(t?.minute == null ? '' : String(t.minute))
  }

  const saveSchedule = async () => {
    if (!editName) { setSchedMsg('先选一个要改时刻的任务'); return }
    try {
      await hubPost(SCHEDULER_CONFIGURE, {
        name: editName, hour: Number(editHour), minute: Number(editMinute),
      })
      setSchedMsg(`${editName}：已改为每天 ${editHour.padStart(2, '0')}:${editMinute.padStart(2, '0')}`)
      loadSched()
    } catch (e) { setSchedMsg(String(e)) }
  }

  return (
    <div className="p-6">
      <PageHeader
        title="定时任务"
        description="调度任务管理（间隔 / cron）"
        actions={<button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"><Plus size={12} /> 新建任务</button>}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {showForm && (
        <Card className="mb-4">
          <div className="grid gap-2 md:grid-cols-5">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="任务名称 *" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={action} onChange={(e) => setAction(e.target.value)} placeholder="动作 (action)" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={interval} onChange={(e) => setInterval(e.target.value)} placeholder="间隔(分钟)" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="cron 表达式(可选)" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <button onClick={create} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500">创建</button>
          </div>
        </Card>
      )}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={tasks}
            keyField="id"
            columns={[
              { key: 'name', title: '任务', render: (r) => <span className="text-slate-200">{String(r.name)}</span> },
              { key: 'action', title: '动作', render: (r) => (r.action ? <span className="font-mono text-xs text-cyan-400">{String(r.action)}</span> : '-') },
              {
                key: 'interval', title: '调度',
                render: (r) => (
                  <span className="font-mono text-xs text-slate-400">
                    {r.cron_expr ? String(r.cron_expr) : r.interval_minutes ? `每 ${r.interval_minutes} 分钟` : '-'}
                  </span>
                ),
              },
              { key: 'enabled', title: '状态', render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '运行中' : '已暂停'}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <div className="flex gap-2">
                    <button onClick={() => (r.enabled ? pause(String(r.id)) : resume(String(r.id)))} className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
                      {r.enabled ? <Pause size={11} /> : <Play size={11} />} {r.enabled ? '暂停' : '恢复'}
                    </button>
                    <button onClick={() => remove(String(r.id))} className="flex items-center gap-1 rounded-md border border-red-900/60 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-950">
                      <Trash2 size={11} /> 删除
                    </button>
                  </div>
                ),
              },
            ]}
          />
        </Card>
      )}
      <Card className="mt-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium text-slate-200">调度器任务（真实执行）</h3>
          <span className="text-xs text-slate-500">「零 token」= 调用链不含 LLM；改时刻即时生效并写入 data/scheduler_overrides.json</span>
        </div>
        {schedMsg && <div className="mb-2 text-xs text-cyan-400">{schedMsg}</div>}
        {sched.length === 0 ? <div className="text-xs text-slate-500">调度器里还没有任务</div> : (
          <DataTable
            data={sched}
            keyField="name"
            columns={[
              { key: 'name', title: '任务', render: (r) => <span className="text-slate-200">{r.name}</span> },
              {
                key: 'kind', title: '类型',
                render: (r) => <span className="font-mono text-xs text-slate-400">{r.kind === 'cron' ? '每日' : r.kind === 'interval' ? '间隔' : '命令'}</span>,
              },
              { key: 'schedule_text', title: '排程', render: (r) => <span className="font-mono text-xs text-cyan-400">{r.schedule_text}</span> },
              {
                key: 'zero_token', title: '零 token',
                render: (r) => (
                  r.zero_token === true
                    ? <Badge color="green">零 token</Badge>
                    : <Badge color="slate">{r.zero_token === false ? '含 LLM' : '未标注'}</Badge>
                ),
              },
              { key: 'enabled', title: '状态', render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '运行中' : '已暂停'}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button onClick={() => toggleSched(r)} className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
                      {r.enabled ? <Pause size={11} /> : <Play size={11} />} {r.enabled ? '暂停' : '恢复'}
                    </button>
                    {/* id 基的两条（agent.task_scheduler 同族）：立即执行 / 启停 */}
                    <button
                      onClick={() => void runNow(r)}
                      disabled={!r.task_id || busyRun !== ''}
                      aria-label={'立即执行 ' + r.name}
                      className="flex items-center gap-1 rounded-md border border-cyan-800/70 px-2.5 py-1.5 text-xs text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-50"
                    >
                      {busyRun === r.task_id ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />} 立即执行
                    </button>
                    {/* 不做「按 id 启停」：SCHEDULER_CONFIGURE 已能按名翻转 enabled，
                        同一调度器再给一条 id 基的同效通道，只会让人以为两者行为不同。 */}
                  </div>
                ),
              },
            ]}
          />
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <select
            value={editName}
            onChange={(e) => pickTask(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 outline-none"
          >
            <option value="">选择要改时刻的任务…</option>
            {sched.filter((t) => t.editable_time).map((t) => (
              <option key={t.name} value={t.name}>{t.name}（{t.schedule_text}）</option>
            ))}
          </select>
          <input value={editHour} onChange={(e) => setEditHour(e.target.value)} placeholder="时 0-23" className="w-24 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 placeholder-slate-600 outline-none" />
          <input value={editMinute} onChange={(e) => setEditMinute(e.target.value)} placeholder="分 0-59" className="w-24 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 placeholder-slate-600 outline-none" />
          <button onClick={saveSchedule} className="rounded-lg bg-cyan-600 px-3 py-2 text-xs text-white hover:bg-cyan-500">保存时刻</button>
        </div>
      </Card>

      {/* 另一族调度器（agent.scheduling）的执行历史。
          为什么单独一块：上面这张表来自 agent.task_scheduler（cron/interval python 任务），
          而本页的「删除/暂停」走的是 /api/schedules/<id>（agent.scheduling）。两个调度器，
          两块数据；合成一张表会让人分不清"这条记录是哪族跑出来的"。 */}
      <Card className="mt-4" title="执行记录（/api/schedules 那一族调度器）">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <HistoryIcon size={11} className="text-cyan-400" />
          <span>与上方任务表**不是同一个调度器** —— 这里只列通过 /api/schedules 登记的调度器跑出的记录。</span>
          <button
            type="button"
            onClick={() => { setHistOpen((v) => !v); if (!histOpen) void loadHist() }}
            className="ml-auto flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
            aria-label="展开执行记录"
          >
            {histBusy ? <Loader2 size={10} className="animate-spin" /> : <HistoryIcon size={10} />} {histOpen ? '收起' : '查看最近 20 条'}
          </button>
        </div>
        {histOpen && (
          <div className="flex flex-col gap-1">
            {histRows.map((h, i) => (
              <div key={String(h.id ?? h.task_id ?? i)} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <span className="font-mono text-[10px] text-slate-500">{String(h.finished_at ?? h.timestamp ?? h.started_at ?? '')}</span>
                <span className="text-slate-300">{String(h.name ?? h.task_name ?? h.task_id ?? '')}</span>
                <Badge color={h.ok === false || h.success === false ? 'red' : 'green'}>
                  {h.ok === false || h.success === false ? '失败' : '成功'}
                </Badge>
                <span className="truncate text-[10px] text-slate-600">{String(h.error ?? h.result ?? '')}</span>
              </div>
            ))}
            {histRows.length === 0 && <div className="text-[11px] text-slate-500">没有记录。</div>}
          </div>
        )}
      </Card>
    </div>
  )
}
