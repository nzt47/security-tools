/**
 * LLM 通信监控 —— 从 legacy index.html 移植
 * 数据源：/api/llm-monitor/stats、/api/llm-monitor/records
 * 功能：调用统计（总量/耗时/token）+ 最近记录列表
 */
import { useEffect, useState } from 'react'
import { Radio, RefreshCw, Trash2 } from 'lucide-react'
import { Card, StatCard, DataTable, Badge, PageHeader, hubGet, hubPost, Loading, ErrorBox } from './components/ui'

interface LlmStats {
  enabled: boolean
  total: number
  avg_duration_ms: number
  total_request_tokens: number
  total_response_tokens: number
  buffer_usage: string
  total_cost_estimate?: number
  max_records: number
}

interface LlmRecord {
  id: string
  timestamp?: string
  provider?: string
  model?: string
  source?: string
  duration_ms?: number
  request_tokens?: number
  response_tokens?: number
  status?: string
  [k: string]: unknown
}

export default function LlmMonitorPage() {
  const [stats, setStats] = useState<LlmStats | null>(null)
  const [records, setRecords] = useState<LlmRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      hubGet('/api/llm-monitor/stats').then((r) => (r as { data?: LlmStats }).data ?? (r as unknown as LlmStats)),
      hubGet('/api/llm-monitor/records?limit=50').then((r) => {
        const d = r as { records?: LlmRecord[]; data?: LlmRecord[] }
        return Array.isArray(d.data) ? d.data : Array.isArray(d.records) ? d.records : []
      }),
    ]).then(([s, rec]) => {
      if (s.status === 'fulfilled') setStats(s.value ?? null)
      if (rec.status === 'fulfilled') setRecords(rec.value ?? [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const clear = async () => {
    try {
      await hubPost('/api/llm-monitor/clear')
      load()
    } catch (e) { setError(String(e)) }
  }

  return (
    <div className="p-6">
      <PageHeader
        title="LLM 通信监控"
        description="LLM 调用统计与收发记录"
        actions={
          <div className="flex items-center gap-2">
            <button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RefreshCw size={12} /> 刷新
            </button>
            <button onClick={clear} className="flex items-center gap-1.5 rounded-lg border border-red-900/60 px-3 py-1.5 text-xs text-red-400 hover:bg-red-950">
              <Trash2 size={12} /> 清空
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="总调用" value={stats?.total ?? 0} icon={<Radio size={16} />} />
            <StatCard label="平均耗时" value={stats?.avg_duration_ms ?? 0} unit="ms" icon={<Radio size={16} />} />
            <StatCard label="请求 Tokens" value={stats?.total_request_tokens ?? 0} icon={<Radio size={16} />} color="text-emerald-400" />
            <StatCard label="响应 Tokens" value={stats?.total_response_tokens ?? 0} icon={<Radio size={16} />} color="text-amber-400" />
          </div>

          <Card title={`调用记录（${records.length}）`}>
            <DataTable
              data={records}
              keyField="id"
              columns={[
                { key: 'timestamp', title: '时间', render: (r) => <span className="font-mono text-xs text-slate-400">{String(r.timestamp ?? '')}</span> },
                { key: 'model', title: '模型', render: (r) => <span className="text-slate-200">{String(r.model ?? r.provider ?? '?')}</span> },
                { key: 'source', title: '来源', render: (r) => (r.source ? <Badge color="cyan">{String(r.source)}</Badge> : '-') },
                { key: 'duration_ms', title: '耗时', render: (r) => (r.duration_ms != null ? <span className="font-mono">{Number(r.duration_ms).toFixed(0)}ms</span> : '-') },
                { key: 'status', title: '状态', render: (r) => <Badge color={String(r.status) === 'success' || String(r.status) === 'ok' ? 'green' : 'slate'}>{String(r.status ?? '?')}</Badge> },
              ]}
              empty="暂无 LLM 调用记录（发起对话后出现）"
            />
          </Card>
        </>
      )}
    </div>
  )
}
