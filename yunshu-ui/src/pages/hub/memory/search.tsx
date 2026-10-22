/**
 * 全库检索（记忆管理 · 搜索）—— 三个检索面的**统一入口**
 *
 * 数据源（按面切换，**不跨面混排**）：
 *   · 向量记忆 /api/vector/search      （POST，信封 v2，键 query/top_k）
 *   · 知识库   /api/knowledge/query    （POST，信封 v2，键 question/top_k）
 *   · 经验库   /api/experience/search  （GET，见 lib/experienceApi.searchPreview）
 *
 * 【为什么只做"一个入口、分面展示"，不合并成一张同构列表】
 *   三面结果字段互不相交、分数不可比（记忆的 sqlite-vec/ChromaDB 路径甚至不产生分数；
 *   知识库是归一化 RRF[0,1]；经验库是加权 RRF），且各自有独立的误召回/阈值语义——
 *   合并会掩盖各面的绝对闸门与"空"的不同含义（无命中 / 被阈值拒绝 / 后端不可用）。
 *   决策与取舍见 docs/proposals/ui_information_architecture_review_20261011.md §9 第 4 条（方案 1）。
 */
import { useState } from 'react'
import { Search } from 'lucide-react'
import { Card, Loading, ErrorBox, PageHeader } from '../components/ui'
// 【P1-front 第六批】两个 POST 端点都已带 X-Envelope: v2，改用 postEnvelope 显式解析。
import { postEnvelope } from '@/api/envelope'
// 经验库面复用既有 API 封装（GET + Authorization），不新建 HTTP 层。
import { searchPreview, type ExperienceHit } from '@/lib/experienceApi'

import {
  KNOWLEDGE_QUERY,
  VECTOR_SEARCH,
} from '@/api/endpoints';

type Surface = 'vector' | 'knowledge' | 'experience'

interface Hit {
  content?: string
  text?: string
  title?: string
  score?: number
  source?: string
  verified?: string
  legs?: string[]
  [k: string]: unknown
}

const SURFACES: { value: Surface; label: string }[] = [
  { value: 'vector', label: '向量记忆' },
  { value: 'knowledge', label: '知识库' },
  { value: 'experience', label: '经验库' },
]

export default function MemorySearch() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<Surface>('vector')

  const search = async () => {
    if (!q.trim()) return
    setLoading(true)
    setError('')
    try {
      // 【请求键按**各端点自己的契约**，两个 POST 面读的键并不相同】
      //   · /api/knowledge/query → 后端读 question/top_k ⇒ 取不到 question 直接 HTTP 400
      //   · /api/vector/search   → 后端读 query/top_k ⇒ top_k 取不到则按默认 5 条返回
      // 契约见 plugins/memory.py::api_vector_search 与
      // agent/server_routes/routes_knowledge.py::api_knowledge_query；
      // 机械守卫：tests/unit/test_frontend_request_contract.py。
      const topK = 10
      if (mode === 'vector') {
        const d = await postEnvelope<{ results?: Hit[] }>(VECTOR_SEARCH, { query: q.trim(), top_k: topK })
        setHits(d?.results ?? [])
      } else if (mode === 'knowledge') {
        const d = await postEnvelope<{ hits?: Hit[] }>(KNOWLEDGE_QUERY, { question: q.trim(), top_k: topK })
        setHits(d?.hits ?? [])
      } else {
        const d = await searchPreview(q.trim(), topK)
        if (d && d.ok === false) {
          setHits([])
          setError(d.error || '经验库检索不可用')
        } else {
          setHits((d?.hits ?? []).map((h: ExperienceHit) => ({
            title: h.task_type || h.id,
            content: h.task,
            score: h.score,
            source: 'experience',
            verified: h.verified,
            legs: h.legs,
          })))
        }
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader title="全库检索" description="一个入口，分面检索：向量记忆 / 知识库 / 经验库（各面分数口径不同，故不跨面混排）" />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      <Card>
        <div className="flex gap-2">
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as Surface)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300"
          >
            {SURFACES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="输入检索关键词…"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-hidden focus:border-cyan-600"
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
              {h.title && (
                <div className="mb-1 text-sm font-medium text-slate-200">
                  {String(h.title)}
                  {h.verified && <span className="ml-2 font-mono text-xs text-slate-500">{String(h.verified)}</span>}
                </div>
              )}
              <div className="text-sm text-slate-300">{String(h.content ?? h.text ?? JSON.stringify(h))}</div>
              {h.score != null && <div className="mt-1 font-mono text-xs text-cyan-500">score: {Number(h.score).toFixed(3)}</div>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
