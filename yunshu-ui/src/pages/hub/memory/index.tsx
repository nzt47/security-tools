/**
 * 记忆管理 —— 手动记忆 / 自动记忆 / 向量搜索
 * 数据源：/api/memory/overview、/api/memory/manual、/api/vector/search
 */
import { useEffect, useState } from 'react'
import { Brain, Search, Trash2, Plus } from 'lucide-react'
import { Card, StatCard, Loading, ErrorBox, PageHeader, hubGet, hubPost, pickList, pickObj } from '../components/ui'

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

export default function MemoryPage() {
  const [overview, setOverview] = useState<MemoryOverview | null>(null)
  const [manualText, setManualText] = useState('')
  const [saving, setSaving] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    hubGet('/api/memory/overview').then((r) => {
      setOverview(pickObj<MemoryOverview>(r) ?? {})
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }, [])

  const saveManual = async () => {
    if (!manualText.trim() || saving) return
    setSaving(true)
    setMsg('')
    try {
      const r = await hubPost('/api/memory/manual', { content: manualText.trim() })
      setMsg(`已保存手动记忆：${JSON.stringify(r).slice(0, 80)}`)
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
      const r = await hubPost('/api/vector/search', { query: searchQ.trim(), limit: 10 })
      setResults(pickList<SearchResult>(r, 'results'))
      setLoading(false)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader title="记忆管理" description="手动记忆 / 自动记忆 / 知识检索" />
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
