/**
 * 心跳监测 —— 全维度健康检查
 * 数据源：/api/heartbeat、/api/heartbeat/history
 */
import { useEffect, useState } from 'react'
import { HeartPulse, RefreshCw } from 'lucide-react'
import { Card, StatCard, Loading, ErrorBox, Badge, PageHeader, hubGet, pickList, pickObj } from '../components/ui'

interface Heartbeat {
  status?: string
  ok?: boolean
  timestamp?: string
  checks?: Record<string, { status?: string; score?: number; detail?: string }>
  [k: string]: unknown
}

interface HistoryItem {
  timestamp?: string
  status?: string
  [k: string]: unknown
}

export default function EngineHeartbeat() {
  const [hb, setHb] = useState<Heartbeat | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      hubGet('/api/heartbeat').then((r) => pickObj<Heartbeat>(r) ?? (r as unknown as Heartbeat)),
      // /api/heartbeat/history 返回 {history: [...], limit, offset, total}
      hubGet('/api/heartbeat/history').then((r) => pickList<HistoryItem>(r, 'history')),
    ]).then(([h, hh]) => {
      if (h.status === 'fulfilled') setHb(h.value)
      if (hh.status === 'fulfilled') setHistory(hh.value ?? [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(() => {
    load()
    // 心跳监测自动刷新（每 30s）
    const timer = setInterval(load, 30000)
    return () => clearInterval(timer)
  }, [])

  const checks = hb?.checks ?? {}

  return (
    <div className="p-6">
      <PageHeader
        title="心跳监测"
        description="全维度健康检查与历史记录"
        actions={<button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"><RefreshCw size={12} /> 立即检测</button>}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="整体状态" value={hb?.ok ? '健康' : hb?.status ?? '-'} icon={<HeartPulse size={16} />} color={hb?.ok ? 'text-emerald-400' : 'text-amber-400'} />
            <StatCard label="检查项" value={Object.keys(checks).length} icon={<HeartPulse size={16} />} />
            <StatCard label="正常项" value={Object.values(checks).filter((c) => String(c?.status ?? '') !== 'failed').length} icon={<HeartPulse size={16} />} color="text-emerald-400" />
            <StatCard label="异常项" value={Object.values(checks).filter((c) => String(c?.status ?? '') === 'failed').length} icon={<HeartPulse size={16} />} color="text-red-400" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="检查明细">
              <div className="space-y-2">
                {Object.entries(checks).length === 0 && <div className="py-6 text-center text-sm text-slate-600">无检查数据</div>}
                {Object.entries(checks).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                    <div>
                      <div className="text-sm text-slate-200">{k}</div>
                      {v?.detail && <div className="text-xs text-slate-500">{String(v.detail)}</div>}
                    </div>
                    <Badge color={String(v?.status ?? '') === 'failed' ? 'red' : v?.status ? 'green' : 'slate'}>
                      {String(v?.status ?? v?.score ?? '?')}
                    </Badge>
                  </div>
                ))}
              </div>
            </Card>

            <Card title="历史记录">
              <div className="max-h-96 space-y-1 overflow-y-auto font-mono text-xs">
                {history.length === 0 && <div className="py-6 text-center text-slate-600">暂无历史</div>}
                {history.map((h, i) => (
                  <div key={i} className="flex gap-3 rounded px-2 py-1 hover:bg-slate-800/40">
                    <span className="shrink-0 text-slate-600">{String(h.timestamp ?? '')}</span>
                    <Badge color={String(h.status) === 'ok' ? 'green' : 'slate'}>{String(h.status ?? '')}</Badge>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
