/**
 * 引擎 —— 心跳监测 / 定时任务 / 执行历史 三 Tab 合一（2026-10-17 IA）
 *
 * 【为什么合并】心跳是「观测产出」，定时任务是「调度配置」，执行历史是两者的交集。
 * 三者同属「引擎」域，分开挂三个导航项只是把同一件事拆成三处找；
 * 合成一页后：默认落在只读的心跳视图，**所有写操作（启停/改时刻）只在「定时任务」Tab 内**。
 *
 * 【旧深链】`?panel=engine/heartbeat` / `?panel=engine/scheduler` 经
 * LEGACY_NAV_KEY_MAP 归一为 `engine`；本组件再读一次原始 hash 决定初始 Tab。
 */
import { lazy, Suspense, useEffect, useState } from 'react'
import { HeartPulse, CalendarClock, History as HistoryIcon } from 'lucide-react'

import { Card, Loading, ErrorBox, DataTable, Badge } from '../components/ui'
import { SCHEDULER_HISTORY } from '@/api/endpoints'
import { getEnvelope } from '@/api/envelope'

const HeartbeatPanel = lazy(() => import('./heartbeat'))
const SchedulerPanel = lazy(() => import('./scheduler'))

type Tab = 'heartbeat' | 'scheduler' | 'history'

const TABS: Array<{ key: Tab; label: string; icon: typeof HeartPulse }> = [
  { key: 'heartbeat', label: '心跳监测', icon: HeartPulse },
  { key: 'scheduler', label: '定时任务', icon: CalendarClock },
  { key: 'history', label: '执行历史', icon: HistoryIcon },
]

interface HistoryRow {
  name?: string
  status?: string
  start_time?: string
  duration_ms?: number
  error?: string
  task_id?: string
  [k: string]: unknown
}

/** 从地址栏原始 hash 读初始 Tab（导航 key 归一后已丢失 engine/* 信息） */
function initialTab(): Tab {
  try {
    const raw = window.location.hash || ''
    const q = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    const panel = new URLSearchParams(q).get('panel') || ''
    return panel === 'engine/scheduler' ? 'scheduler' : 'heartbeat'
  } catch {
    return 'heartbeat'
  }
}

export default function EngineHub() {
  const [tab, setTab] = useState<Tab>(initialTab)
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (tab !== 'history') return
    setLoading(true)
    setError('')
    getEnvelope<{ history?: HistoryRow[] }>(SCHEDULER_HISTORY)
      .then((d) => setHistory(d?.history ?? []))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [tab])

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-6 pt-4">
        {TABS.map((t) => {
          const Icon = t.icon
          const active = tab === t.key
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={
                'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ' +
                (active
                  ? 'bg-cyan-600 text-white'
                  : 'border border-slate-700 text-slate-300 hover:bg-slate-800')
              }
            >
              <Icon size={12} /> {t.label}
            </button>
          )
        })}
        <span className="ml-2 text-xs text-slate-500">
          只读视图默认打开；启停与改时刻只在「定时任务」内
        </span>
      </div>

      {tab === 'history' ? (
        <div className="p-6">
          {error && <div className="mb-4"><ErrorBox message={error} /></div>}
          {loading ? <Loading /> : (
            <Card>
              <DataTable
                data={history}
                keyField="start_time"
                columns={[
                  {
                    key: 'start_time', title: '时间',
                    render: (r) => <span className="font-mono text-xs text-slate-400">{String(r.start_time ?? '-')}</span>,
                  },
                  { key: 'name', title: '任务', render: (r) => <span className="text-slate-200">{String(r.name ?? '-')}</span> },
                  {
                    key: 'status', title: '状态',
                    render: (r) => (
                      <Badge color={r.status === 'success' ? 'green' : r.status === 'error' ? 'red' : 'slate'}>
                        {String(r.status ?? '-')}
                      </Badge>
                    ),
                  },
                  {
                    key: 'duration_ms', title: '耗时',
                    render: (r) => <span className="font-mono text-xs text-slate-400">{r.duration_ms == null ? '-' : String(r.duration_ms) + ' ms'}</span>,
                  },
                  {
                    key: 'error', title: '错误',
                    render: (r) => (
                      <span className="text-xs text-red-400">{r.error ? String(r.error).slice(0, 80) : '-'}</span>
                    ),
                  },
                ]}
              />
            </Card>
          )}
        </div>
      ) : (
        <Suspense fallback={<Loading />}>
          {tab === 'heartbeat' ? <HeartbeatPanel /> : <SchedulerPanel />}
        </Suspense>
      )}
    </div>
  )
}
