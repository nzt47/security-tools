/**
 * LlmMonitorPanel —— 并入提示词实验室的「LLM 通信监控」区
 * ------------------------------------------------------------------
 * 原工作台「人格与提示词 → LLM 通信」页面功能收拢于此：
 *   GET  /api/llm-monitor/stats           调用统计（总量/平均耗时/请求与响应 tokens）
 *   GET  /api/llm-monitor/records?limit=50 最近收发记录
 *   POST /api/llm-monitor/clear           清空记录
 * 挂载后每 10s 自动刷新（监控语义），亦提供手动刷新。
 */
import { useCallback, useEffect, useState } from 'react'
import { Radio, RefreshCw, Trash2 } from 'lucide-react'
import { Card, StatCard, DataTable, Badge, hubGet, hubPost } from '../hub/components/ui'

const AUTO_REFRESH_MS = 10_000

interface LlmStats {
  enabled?: boolean
  total?: number
  avg_duration_ms?: number
  total_request_tokens?: number
  total_response_tokens?: number
  buffer_usage?: string
  total_cost_estimate?: number
  max_records?: number
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

const unwrap = <T,>(r: unknown, fallback: T): T => {
  const rr = r as { data?: T }
  return rr?.data ?? (r as unknown as T) ?? fallback
}

export default function LlmMonitorPanel() {
  const [stats, setStats] = useState<LlmStats | null>(null)
  const [records, setRecords] = useState<LlmRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      const [s, rec] = await Promise.allSettled([
        hubGet<LlmStats | { data?: LlmStats }>('/api/llm-monitor/stats'),
        hubGet<unknown>('/api/llm-monitor/records?limit=50'),
      ])
      if (s.status === 'fulfilled') setStats(unwrap<LlmStats>(s.value, {} as LlmStats))
      if (rec.status === 'fulfilled') {
        const d = rec.value as { records?: LlmRecord[]; data?: LlmRecord[] }
        setRecords(Array.isArray(d.data) ? d.data : Array.isArray(d.records) ? d.records : [])
      }
      let fail: unknown = null
      if (s.status === 'rejected') fail = s.reason
      if (rec.status === 'rejected') fail = rec.reason
      if (fail) setError('LLM 通信数据加载失败：' + String(fail))
    } catch (e) {
      setError(`LLM 通信数据加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), AUTO_REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  const clear = async () => {
    try {
      await hubPost('/api/llm-monitor/clear')
      setMsg('LLM 通信记录已清空')
      await load()
    } catch (e) {
      setError(`清空失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <section className="pl-category pl-llm-monitor">
      <h2 className="pl-category-title" style={{ color: '#f472b6' }}>
        <span className="pl-category-dot" style={{ background: '#f472b6' }} />
        LLM 通信监控
        <span className="pl-category-count">
          原工作台「人格与提示词 → LLM 通信」并入 · 每 10s 自动刷新
        </span>
      </h2>
      <p className="pl-category-desc">
        每次真实 LLM 调用的统计与最近收发记录（在实验室里直接观察提示词改动对线上调用/耗时的效果）。
      </p>

      {error && <p className="pl-error">{error}</p>}
      {msg && <p className="pl-id-msg">{msg}</p>}

      <div className="pl-monitor-toolbar">
        <button type="button" className="pl-btn" onClick={() => void load()} title="立即刷新">
          <RefreshCw size={12} /> 刷新
        </button>
        <button type="button" className="pl-btn" onClick={() => void clear()} title="清空 LLM 调用记录">
          <Trash2 size={12} /> 清空
        </button>
        {loading && <span className="pl-id-hint">加载中…</span>}
        {stats?.enabled === false && <span className="pl-id-hint">（监控未启用，接口可能返回空数据）</span>}
      </div>

      <div className="pl-monitor-stats">
        <StatCard label="总调用" value={stats?.total ?? 0} icon={<Radio size={15} />} />
        <StatCard label="平均耗时" value={stats?.avg_duration_ms ?? 0} unit="ms" icon={<Radio size={15} />} />
        <StatCard label="请求 Tokens" value={stats?.total_request_tokens ?? 0} icon={<Radio size={15} />} color="text-emerald-400" />
        <StatCard label="响应 Tokens" value={stats?.total_response_tokens ?? 0} icon={<Radio size={15} />} color="text-amber-400" />
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
    </section>
  )
}
