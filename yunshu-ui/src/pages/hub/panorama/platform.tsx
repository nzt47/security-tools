/**
 * 平台能力（C-3）
 * ------------------------------------------------------------------
 * Owner 2026-11-01：平台能力（服务 / 审计 / 调度 / 监控）是**运维对象**，该单独开一个叶子，
 * 不进「能力卡」（能力卡第一 Tab 是 LLM 技能，第二 Tab 是**元生功能** = 智能体天生会做的事）。
 *
 * 【为什么不造新端点】#1394 曾经加过 /api/system/platform-cards，方向错了已被撤（§11.36 K）。
 * 本页只**复用**三个既有只读端点：
 *   · GET /api/system/units        功能单位（12 个）+ 健康分（与「功能单位」看板同源）
 *   · GET /api/tools/config        工具清单（94 个，enabled 决定 off）
 *   · GET /api/scheduler/overview  调度任务（15 个，zero_token 标注）
 *
 * 【纪律】后端没给的字段就显示"—"，不在前端重算（沿用 health.tsx 的口径）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, Cpu, ListChecks, RefreshCw, Search } from 'lucide-react'
import { Badge, Card, ErrorBox, Loading, PageHeader, hubGet, unwrap } from '../components/ui'
import { SCHEDULER_OVERVIEW, SYSTEM_UNITS, TOOLS_CONFIG } from '@/api/endpoints'

type Unit = { id: string; status: string; headline?: string; detail?: string }
type UnitsResp = {
  ok?: boolean
  units?: Unit[]
  summary?: { ok?: number; warn?: number; never?: number; error?: number; total?: number }
  health_score?: number
}

type Tool = { name: string; description?: string; enabled?: boolean; call_count?: number }
type Task = {
  task_id: string
  name: string
  enabled: boolean
  schedule_text?: string
  zero_token?: boolean
  last_run?: string | null
  owner?: string
}

type Tab = 'units' | 'tools' | 'tasks'

const STATUS_COLOR: Record<string, 'green' | 'amber' | 'red' | 'slate'> = {
  ok: 'green',
  warn: 'amber',
  error: 'red',
  never: 'slate',
  off: 'slate',
}

export default function PlatformCapability() {
  const [tab, setTab] = useState<Tab>('units')
  const [units, setUnits] = useState<UnitsResp>({})
  const [tools, setTools] = useState<Tool[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [u, t, s] = await Promise.all([
        hubGet<UnitsResp>(SYSTEM_UNITS),
        hubGet<Record<string, unknown>>(TOOLS_CONFIG),
        hubGet<Record<string, unknown>>(SCHEDULER_OVERVIEW),
      ])
      setUnits((u || {}) as UnitsResp)
      const toolList = (unwrap<{ tools?: Tool[] }>(t) || {}) as { tools?: Tool[] }
      setTools(Array.isArray(toolList.tools) ? toolList.tools : [])
      const sched = (unwrap<{ tasks?: Task[] }>(s) || {}) as { tasks?: Task[] }
      setTasks(Array.isArray(sched.tasks) ? sched.tasks : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const unitList = units.units || []
  const summary = units.summary || {}
  const kw = q.trim().toLowerCase()

  const filteredTools = useMemo(() => {
    if (!kw) return tools
    return tools.filter((t) => `${t.name} ${t.description || ''}`.toLowerCase().includes(kw))
  }, [tools, kw])

  const filteredTasks = useMemo(() => {
    if (!kw) return tasks
    return tasks.filter((t) => `${t.name} ${t.owner || ''} ${t.schedule_text || ''}`.toLowerCase().includes(kw))
  }, [tasks, kw])

  const enabledTools = tools.filter((t) => t.enabled).length
  const disabledTools = tools.length - enabledTools
  const enabledTasks = tasks.filter((t) => t.enabled).length

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <PageHeader
        title="平台能力"
        description="云枢自己提供的功能（运维对象）：功能单位 / 工具 / 调度。与「能力卡」分开——那边是 LLM 技能与元生功能。"
        actions={
          <button
            type="button"
            onClick={() => void load()}
            className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-slate-800"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        }
      />

      <div className="flex items-center gap-1 border-b border-slate-800 px-4 py-2">
        <button
          type="button"
          onClick={() => { setTab('units'); setQ('') }}
          className={tab === 'units'
            ? 'flex items-center gap-1 rounded-md border border-cyan-700/70 bg-cyan-500/10 px-2.5 py-1 text-[11px] text-cyan-300'
            : 'flex items-center gap-1 rounded-md border border-slate-800 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800'}
        >
          <Activity size={11} /> 功能单位 {unitList.length ? `(${unitList.length})` : ''}
        </button>
        <button
          type="button"
          onClick={() => { setTab('tools'); setQ('') }}
          className={tab === 'tools'
            ? 'flex items-center gap-1 rounded-md border border-violet-700/70 bg-violet-500/10 px-2.5 py-1 text-[11px] text-violet-300'
            : 'flex items-center gap-1 rounded-md border border-slate-800 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800'}
        >
          <Cpu size={11} /> 工具 {tools.length ? `(${tools.length})` : ''}
        </button>
        <button
          type="button"
          onClick={() => { setTab('tasks'); setQ('') }}
          className={tab === 'tasks'
            ? 'flex items-center gap-1 rounded-md border border-amber-700/70 bg-amber-500/10 px-2.5 py-1 text-[11px] text-amber-300'
            : 'flex items-center gap-1 rounded-md border border-slate-800 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800'}
        >
          <ListChecks size={11} /> 调度 {tasks.length ? `(${tasks.length})` : ''}
        </button>
        {tab !== 'units' ? (
          <div className="ml-auto flex items-center gap-1 rounded-md border border-slate-800 px-2 py-1">
            <Search size={11} className="text-slate-500" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索"
              className="w-40 bg-transparent text-[11px] text-slate-200 outline-none placeholder:text-slate-600"
            />
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error ? <ErrorBox message={error} /> : null}
        {loading && !unitList.length && !tools.length ? <Loading /> : null}

        {tab === 'units' ? (
          <Card>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              <span>健康分 <strong className="text-slate-100">{units.health_score ?? '—'}</strong></span>
              <span>ok <strong className="text-emerald-300">{summary.ok ?? '—'}</strong></span>
              <span>warn <strong className="text-amber-300">{summary.warn ?? '—'}</strong></span>
              <span>never <strong className="text-slate-300">{summary.never ?? '—'}</strong></span>
              <span>error <strong className="text-rose-300">{summary.error ?? '—'}</strong></span>
            </div>
            <ul className="divide-y divide-slate-800">
              {unitList.map((u) => (
                <li key={u.id} className="flex items-start gap-2 py-1.5">
                  <Badge color={STATUS_COLOR[u.status] || 'slate'}>{u.status}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] text-slate-200">{u.id}</div>
                    {u.headline ? <div className="text-[10px] text-slate-500">{u.headline}</div> : null}
                  </div>
                </li>
              ))}
              {unitList.length === 0 && !loading ? <li className="py-4 text-[11px] text-slate-500">没有数据</li> : null}
            </ul>
          </Card>
        ) : null}

        {tab === 'tools' ? (
          <Card>
            <div className="mb-3 text-[11px] text-slate-400">
              共 <strong className="text-slate-100">{tools.length}</strong> 个 · 启用{' '}
              <strong className="text-emerald-300">{enabledTools}</strong> · 停用{' '}
              <strong className="text-slate-400">{disabledTools}</strong>
              <span className="ml-2 text-slate-600">（停用的不会出现在模型可调用面）</span>
            </div>
            <ul className="divide-y divide-slate-800">
              {filteredTools.map((t) => (
                <li key={t.name} className="flex items-start gap-2 py-1.5">
                  <Badge color={t.enabled ? 'green' : 'slate'}>{t.enabled ? 'on' : 'off'}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] text-slate-200">{t.name}</div>
                    {t.description ? (
                      <div className="line-clamp-2 text-[10px] text-slate-500">{t.description}</div>
                    ) : null}
                  </div>
                  <span className="shrink-0 text-[10px] text-slate-600">调用 {t.call_count ?? 0}</span>
                </li>
              ))}
              {filteredTools.length === 0 ? <li className="py-4 text-[11px] text-slate-500">没有匹配项</li> : null}
            </ul>
          </Card>
        ) : null}

        {tab === 'tasks' ? (
          <Card>
            <div className="mb-3 text-[11px] text-slate-400">
              共 <strong className="text-slate-100">{tasks.length}</strong> 个 · 启用{' '}
              <strong className="text-emerald-300">{enabledTasks}</strong> · 零 token{' '}
              <strong className="text-cyan-300">{tasks.filter((t) => t.zero_token).length}</strong>
            </div>
            <ul className="divide-y divide-slate-800">
              {filteredTasks.map((t) => (
                <li key={t.task_id} className="flex items-start gap-2 py-1.5">
                  <Badge color={t.enabled ? 'green' : 'slate'}>{t.enabled ? 'on' : 'off'}</Badge>
                  {t.zero_token ? <Badge color="cyan">0-token</Badge> : null}
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] text-slate-200">{t.name}</div>
                    <div className="text-[10px] text-slate-500">
                      {t.schedule_text || '—'} · 上次 {t.last_run || '—'}
                    </div>
                  </div>
                </li>
              ))}
              {filteredTasks.length === 0 ? <li className="py-4 text-[11px] text-slate-500">没有匹配项</li> : null}
            </ul>
          </Card>
        ) : null}
      </div>
    </div>
  )
}
