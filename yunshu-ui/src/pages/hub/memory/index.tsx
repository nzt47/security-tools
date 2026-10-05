/**
 * 记忆管理 —— 手动记忆 / 自动记忆 / 向量搜索
 * 数据源：/api/memory/overview、/api/memory/manual、/api/vector/search
 *
 * 参数化（缺陷 ②）：同一组件被工作台 memory/manual 与 memory/auto 两个导航项复用，
 * 由导航 key 推导的 mode 决定页面初始模式（manual=手动录入卡片 / auto=自动沉淀说明），
 * 点击不同菜单不再渲染成相同内容（配合 ContentPanel 的 key 重挂载）。
 */
import { useEffect, useState } from 'react'
import { Brain, Search, Plus } from 'lucide-react'
import { Card, StatCard, Loading, ErrorBox, PageHeader } from '../components/ui'
// 【P1-front 第六批】三个端点都已带 X-Envelope: v2（overview 是 GET，另两个是 POST），
// 改用显式信封解析：GET 走 getEnvelope、POST 走 postEnvelope（后者的请求头语义与 hubPost 逐条对齐）。
import { getEnvelope, postEnvelope } from '@/api/envelope'

import {
  MEMORY_MANUAL,
  MEMORY_OVERVIEW,
  VECTOR_SEARCH,
} from '@/api/endpoints';

interface MemoryOverview {
  message_count?: number
  summary?: string
  window_events?: number
  vector_docs?: number
  [k: string]: unknown
}

interface SearchResult {
  content?: string
  text?: string
  score?: number
  [k: string]: unknown
}

interface MemoryPageProps {
  /** 初始模式（默认 manual）；由工作台导航 key 推导：memory/manual → manual、memory/auto → auto */
  mode?: 'manual' | 'auto'
}

export default function MemoryPage({ mode = 'manual' }: MemoryPageProps) {
  const isAuto = mode === 'auto'
  const [overview, setOverview] = useState<MemoryOverview | null>(null)
  const [manualText, setManualText] = useState('')
  const [saving, setSaving] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    // 显式信封解析：data 就是概览对象本身
    getEnvelope<MemoryOverview | null>(MEMORY_OVERVIEW).then((d) => {
      setOverview(d ?? {})
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }, [])

  const saveManual = async () => {
    if (!manualText.trim() || saving) return
    setSaving(true)
    setMsg('')
    try {
      // 迁移前这里直接把**整个响应体**塞进提示文案；现在响应体是信封，
      // 故取 data（业务体）再序列化 —— 提示里不再出现 code/message 这些传输层字段。
      const d = await postEnvelope<{ ok?: boolean }>(MEMORY_MANUAL, { content: manualText.trim() })
      setMsg(`已保存手动记忆：${JSON.stringify(d).slice(0, 80)}`)
      setManualText('')
    } catch (e) {
      setMsg(`保存失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setSaving(false)
    }
  }

  const doSearch = async () => {
    if (!searchQ.trim()) return
    setLoading(true)
    try {
      // 【已知不一致，本次不动】后端 api_vector_search 读的是 `top_k`（默认 5），
      // 而这里一直传的是 `limit` ⇒ 10 这个值**从未生效**（实际取 5 条）。
      // 改它属于「改请求契约」，与本批「只改响应解析」不是一件事，故留原样并在此标注。
      const d = await postEnvelope<{ results?: SearchResult[] }>(VECTOR_SEARCH, { query: searchQ.trim(), limit: 10 })
      setResults(d?.results ?? [])
      setLoading(false)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader
        title={isAuto ? '自动记忆' : '记忆管理'}
        description={isAuto ? '系统在对话过程中自动沉淀的记忆 / 知识检索' : '手动记忆 / 自动记忆 / 知识检索'}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{msg}</div>}

      {loading && !overview ? <Loading /> : (
        <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="消息数" value={overview?.message_count ?? '-'} icon={<Brain size={16} />} />
          <StatCard label="向量文档" value={overview?.vector_docs ?? '-'} icon={<Brain size={16} />} color="text-emerald-400" />
          <StatCard label="窗口事件" value={overview?.window_events ?? '-'} icon={<Brain size={16} />} color="text-amber-400" />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {isAuto ? (
          <Card title="自动记忆">
            <div className="space-y-3 text-sm">
              <p className="leading-6 text-slate-400">
                自动记忆由系统在对话过程中自动沉淀，无需手动录入：
                关键信息 → 窗口事件 → 摘要 → 向量化入库，供后续知识检索复用。
                当前共沉淀{' '}
                <span className="font-mono text-cyan-300">{overview?.window_events ?? '-'}</span>{' '}
                条窗口事件、
                <span className="font-mono text-cyan-300">{overview?.vector_docs ?? '-'}</span>{' '}
                条向量文档。
              </p>
              <div className="rounded-lg border border-cyan-900/50 bg-cyan-950/20 px-4 py-3 text-xs leading-6 text-cyan-200/80">
                提示：如需手动录入关键信息，请在左侧导航切换至「手动记忆」（memory/manual）。
              </div>
            </div>
          </Card>
        ) : (
          <Card title="手动记忆">
            <textarea
              value={manualText}
              onChange={(e) => setManualText(e.target.value)}
              rows={5}
              placeholder="输入要记住的内容…"
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
            />
            <button
              onClick={saveManual}
              disabled={saving || !manualText.trim()}
              className="mt-3 flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-40"
            >
              <Plus size={14} /> {saving ? '保存中…' : '保存记忆'}
            </button>
          </Card>
        )}

        <Card title="知识检索">
          <div className="flex gap-2">
            <input
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()}
              placeholder="搜索记忆 / 知识…"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
            />
            <button onClick={doSearch} className="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800">
              <Search size={14} /> 搜索
            </button>
          </div>
          <div className="mt-3 space-y-2">
            {results.length === 0 && <div className="py-6 text-center text-sm text-slate-600">输入关键词搜索记忆</div>}
            {results.map((r, i) => (
              <div key={i} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-sm">
                <div className="text-slate-300">{String(r.content ?? r.text ?? JSON.stringify(r))}</div>
                {r.score != null && <div className="mt-1 font-mono text-xs text-cyan-500">score: {Number(r.score).toFixed(3)}</div>}
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
