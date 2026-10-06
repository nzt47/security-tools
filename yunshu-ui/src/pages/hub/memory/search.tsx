/**
 * 记忆搜索 —— 全局知识检索
 * 数据源：/api/vector/search、/api/knowledge/query
 */
import { useState } from 'react'
import { Search } from 'lucide-react'
import {  Card, Loading, ErrorBox, PageHeader } from '../components/ui'
// 【P1-front 第六批】两个端点都是 POST 且已带 X-Envelope: v2，改用 postEnvelope 显式解析。
import { postEnvelope } from '@/api/envelope'

import {
  KNOWLEDGE_QUERY,
  VECTOR_SEARCH,
} from '@/api/endpoints';

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
      // 【请求键按**各端点自己的契约**，两个端点读的键并不相同】
      // 原先两边都发 { query, limit }，对活体服务打真实请求实测：
      //   · /api/knowledge/query → 后端读 question/top_k ⇒ 取不到 question 直接 **HTTP 400**
      //     「查询问题不能为空」（「知识库」页签因此一直是报错态，而不是空列表）；
      //   · /api/vector/search   → 后端读 query/top_k ⇒ top_k 取不到，永远按默认 5 条返回
      //     （前端写的 limit: 10 **从未生效**）。
      // 契约见 plugins/memory.py::api_vector_search 与
      // agent/server_routes/routes_knowledge.py::api_knowledge_query；
      // 机械守卫：tests/unit/test_frontend_request_contract.py（把「前端发的键」与「后端读的键」对拍）。
      const topK = 10
      if (mode === 'vector') {
        const d = await postEnvelope<{ results?: Hit[] }>(VECTOR_SEARCH, { query: q.trim(), top_k: topK })
        setHits(d?.results ?? [])
      } else {
        // 【第六批只修了一半】后端返回的键是 hits（不是 results）—— 那次只改了**响应**解析，
        // 请求侧仍是错的（见上），所以这条页签当时只是从「静默空」变成了「显示 400」。
        const d = await postEnvelope<{ hits?: Hit[] }>(KNOWLEDGE_QUERY, { question: q.trim(), top_k: topK })
        setHits(d?.hits ?? [])
      }
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
