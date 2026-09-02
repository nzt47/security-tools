/**
 * 定时任务 —— 调度任务管理
 * 数据源：/api/schedules（列表/创建/暂停/恢复/删除）
 */
import { useEffect, useState } from 'react'
import { CalendarClock, Plus, Trash2, Pause, Play } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, unwrap , pickList } from '../components/ui'

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

export default function EngineScheduler() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [action, setAction] = useState('')
  const [interval, setInterval] = useState('30')
  const [cron, setCron] = useState('')

  const load = () => {
    setLoading(true)
    hubGet('/api/schedules').then((r) => {
      setTasks(pickList<Task>(r, 'tasks'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const create = async () => {
    try {
      await hubPost('/api/schedules', {
        name, action, interval_minutes: Number(interval) || 0, cron_expr: cron,
      })
      setShowForm(false)
      setName(''); setAction(''); setCron('')
      load()
    } catch (e) { setError(String(e)) }
  }

  const pause = async (id: string) => {
    try { await hubPost(`/api/schedules/${id}/pause`); load() } catch (e) { setError(String(e)) }
  }
  const resume = async (id: string) => {
    try { await hubPost(`/api/schedules/${id}/resume`); load() } catch (e) { setError(String(e)) }
  }
  const remove = async (id: string) => {
    try { await hubPost(`/api/schedules/${id}/delete`, {}); load() } catch (e) { setError(String(e)) }
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
    </div>
  )
}
