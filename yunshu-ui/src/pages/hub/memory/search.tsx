/**
 * 记忆搜索 —— 全局知识检索
 * 数据源：/api/vector/search、/api/knowledge/query
 */
import { useState } from 'react'
import { Search } from 'lucide-react'
import {  Card, Loading, ErrorBox, PageHeader, hubPost , pickList } from '../components/ui'

interface Hit {
  content?: string
  text?: string
  title?: string
  score?: number
  source?: string
  [k: string]: unknown
}

export default function MemorySearch() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<'vector' | 'knowledge'>('vector')

  const search = async () => {
    if (!q.trim()) return
    setLoading(true)
    setError('')
    try {
      const url = mode === 'vector' ? '/api/vector/search' : '/api/knowledge/query'
      const body = mode === 'vector' ? { query: q.trim(), limit: 10 } : { query: q.trim(), limit: 10 }
      const r = await hubPost(url, body)
      setHits(pickList<Hit>(r, 'results'))
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader title="搜索" description="全局记忆与知识检索" />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      <Card>
        <div className="flex gap-2">
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as 'vector' | 'knowledge')}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300"
          >
            <option value="vector">向量记忆</option>
            <option value="knowledge">知识库</option>
          </select>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="输入检索关键词…"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
          />
          <button onClick={search} disabled={loading} className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500">
            <Search size={14} /> 搜索
          </button>
        </div>
        {loading && <div className="mt-4"><Loading /></div>}
        <div className="mt-4 space-y-2">
          {hits.length === 0 && !loading && <div className="py-8 text-center text-sm text-slate-600">输入关键词开始检索</div>}
          {hits.map((h, i) => (
            <div key={i} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5">
              {h.title && <div className="mb-1 text-sm font-medium text-slate-200">{String(h.title)}</div>}
              <div className="text-sm text-slate-300">{String(h.content ?? h.text ?? JSON.stringify(h))}</div>
              {h.score != null && <div className="mt-1 font-mono text-xs text-cyan-500">score: {Number(h.score).toFixed(3)}</div>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
