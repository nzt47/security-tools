/**
 * 经验库面板（方案 P3 前端 ExperiencePanel）
 * ----------------------------------------------------------------
 * 数据源：/api/experience/*（见 lib/experienceApi.ts）
 * 后端：plugins/experience.py
 *
 * 【与方案的差异】方案原为「从历史会话导入」——P0 实测后改为：
 * 会话→经验的提炼由离线 CLI（extract.py）承担，本面板负责**审阅 / 检索预览 /
 * 统计 / 批次回滚**，ingest 接口保留供 CLI/外部写入。
 *
 * 【UI 纪律】工作台内扩展，不建独立 Web App；复用 pages/hub/components/ui 的
 * Card / StatCard / PageHeader / Loading / ErrorBox / Badge。
 */
import { useCallback, useEffect, useState } from 'react'
import { Search, RefreshCw, Undo2, CheckCircle2, XCircle, Archive } from 'lucide-react'
import { Badge, Card, ErrorBox, Loading, PageHeader, StatCard } from '../components/ui'
import {
  fetchDetail, fetchList, fetchStats, reviewItem, rollbackBatch, searchPreview,
  type ExperienceHit, type ExperienceItem, type ExperienceStats,
} from '@/lib/experienceApi'

const NOTICE = '投喂不等于学会。入库后请用一个真实问题验证是否命中。'
const VERIFIED_COLOR: Record<string, 'green' | 'red' | 'amber' | 'slate'> = {
  pass: 'green', fail: 'red', unverified: 'amber',
}

export default function ExperiencePanel() {
  const [stats, setStats] = useState<ExperienceStats | null>(null)
  const [items, setItems] = useState<ExperienceItem[]>([])
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<ExperienceItem | null>(null)
  const [hits, setHits] = useState<ExperienceHit[]>([])
  const [q, setQ] = useState('')
  const [lang, setLang] = useState('')
  const [status, setStatus] = useState('')
  const [batchId, setBatchId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [s, l] = await Promise.all([
        fetchStats(),
        fetchList({ lang, status, limit: 100 }),
      ])
      setStats(s.stats)
      setItems(l.items || [])
      setTotal(l.total || 0)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [lang, status])

  useEffect(() => { void load() }, [load])

  const onSearch = async () => {
    if (!q.trim()) return
    setError('')
    try {
      const r = await searchPreview(q.trim(), 5, lang || undefined)
      setHits(r.hits || [])
    } catch (e) {
      setError(String(e))
    }
  }

  const onOpen = async (id: string) => {
    setError('')
    try {
      const r = await fetchDetail(id)
      setSelected(r.item)
    } catch (e) {
      setError(String(e))
    }
  }

  const onReview = async (action: 'accept' | 'reject' | 'deprecate') => {
    if (!selected) return
    setError(''); setMsg('')
    try {
      await reviewItem(selected.id, action)
      setMsg(`已 ${action}：${selected.id}`)
      setSelected({ ...selected, review_status: action })
      void load()
    } catch (e) {
      setError(String(e))
    }
  }

  const onRollback = async () => {
    if (!batchId.trim()) return
    setError(''); setMsg('')
    try {
      const r = await rollbackBatch(batchId.trim())
      setMsg(`批次已回滚：移除 ${r.removed ?? 0} 条，剩余 ${r.remaining ?? 0} 条`)
      setBatchId('')
      setSelected(null)
      void load()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="p-6">
      <PageHeader
        title="经验库"
        description="DSH 历史会话提炼的经验样本：审阅 / 检索预览 / 统计 / 批次回滚"
        actions={
          <button onClick={() => void load()} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-cyan-700">
            <RefreshCw size={13} /> 刷新
          </button>
        }
      />

      {/* 方案要求：面板顶部固定提示 */}
      <div data-testid="experience-notice" className="mb-4 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-400">
        {NOTICE}
      </div>

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-emerald-900 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-400">{msg}</div>}

      {/* 统计 */}
      {stats && (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="库规模" value={stats.corpus_size} />
          <StatCard label="已验证 (pass)" value={stats.by_verified?.pass ?? 0} />
          <StatCard label="踩坑条目" value={stats.pitfalls_total} />
          <StatCard label="入库批次" value={stats.batches} />
        </div>
      )}

      {/* 检索预览（本地 BM25，不走 DeepSeek） */}
      <Card className="mb-5">
        <div className="mb-2 text-sm font-medium text-slate-300">检索预览</div>
        <div className="flex gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void onSearch()}
            placeholder="输入一个真实问题，看能否命中…"
            aria-label="检索预览输入"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
          />
          <button onClick={() => void onSearch()} className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500">
            <Search size={14} /> 预览
          </button>
        </div>
        {hits.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {hits.map((h) => (
              <div key={h.id} className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                <span className="font-mono text-xs text-cyan-500">{h.score.toFixed(4)}</span>
                <span className="flex-1 text-xs text-slate-300">{h.task}</span>
                <Badge color={VERIFIED_COLOR[h.verified || ''] || 'slate'}>{h.verified || '?'}</Badge>
                <span className="text-[10px] text-slate-600">{h.legs.join('+')}</span>
              </div>
            ))}
          </div>
        )}
        {q && hits.length === 0 && <div className="mt-3 text-xs text-slate-600">未命中。注意：语料过少时 BM25 可能不返回结果。</div>}
      </Card>

      {/* 批次回滚 */}
      <Card className="mb-5">
        <div className="mb-2 text-sm font-medium text-slate-300">按 batch_id 回滚</div>
        <div className="flex gap-2">
          <input
            value={batchId}
            onChange={(e) => setBatchId(e.target.value)}
            placeholder="batch_id"
            aria-label="批次 ID"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
          />
          <button onClick={() => void onRollback()} disabled={!batchId.trim()} className="flex items-center gap-2 rounded-lg border border-red-900 px-4 py-2 text-sm text-red-400 hover:bg-red-950/40 disabled:opacity-40">
            <Undo2 size={14} /> 回滚
          </button>
        </div>
        <div className="mt-1.5 text-[10px] text-slate-600">回滚会写审计链留痕，batch_id 即回滚凭据。</div>
      </Card>

      {/* 列表 + 详情 */}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-300">样本（{total}）</span>
            <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="语言筛选"
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300">
              <option value="">全部语言</option>
              <option value="python">python</option>
              <option value="typescript">typescript</option>
              <option value="markdown">markdown</option>
              <option value="yaml">yaml</option>
            </select>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="状态筛选"
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300">
              <option value="">全部状态</option>
              <option value="pending">待审</option>
              <option value="accept">已入库</option>
              <option value="reject">已拒收</option>
              <option value="deprecate">已废弃</option>
            </select>
          </div>
          {loading && <Loading />}
          <div className="max-h-[460px] space-y-1.5 overflow-y-auto">
            {items.map((it) => (
              <button key={it.id} onClick={() => void onOpen(it.id)}
                className={`block w-full rounded-lg border px-3 py-2 text-left transition ${selected?.id === it.id ? 'border-cyan-700 bg-cyan-950/20' : 'border-slate-800 bg-slate-900/60 hover:border-slate-700'}`}>
                <div className="flex items-center gap-2">
                  <span className="flex-1 truncate text-xs text-slate-300">{it.task || it.id}</span>
                  <Badge color={VERIFIED_COLOR[it.verified || ''] || 'slate'}>{it.verified || '?'}</Badge>
                </div>
                <div className="mt-1 flex gap-2 font-mono text-[10px] text-slate-600">
                  <span>{it.task_type}</span>
                  <span>{it.stack?.lang}</span>
                  <span>diff×{it.diffs?.length ?? 0}</span>
                  <span>pitfall×{it.pitfalls?.length ?? 0}</span>
                  <span>{it.review_status || 'pending'}</span>
                </div>
              </button>
            ))}
            {!loading && items.length === 0 && <div className="py-8 text-center text-xs text-slate-600">库为空。请先运行 extract.py 产出语料。</div>}
          </div>
        </Card>

        <Card>
          <div className="mb-3 text-sm font-medium text-slate-300">详情</div>
          {!selected && <div className="py-8 text-center text-xs text-slate-600">从左侧选择一个样本</div>}
          {selected && (
            <div className="space-y-3">
              <div className="text-sm text-slate-200">{selected.task}</div>
              <div className="flex flex-wrap gap-1.5">
                <Badge color={VERIFIED_COLOR[selected.verified || ''] || 'slate'}>{selected.verified}</Badge>
                <Badge color="slate">{selected.task_type}</Badge>
                <Badge color="slate">{selected.stack?.lang}</Badge>
                <Badge color="slate">{selected.review_status || 'pending'}</Badge>
              </div>

              <div className="flex gap-2">
                <button onClick={() => void onReview('accept')} className="flex items-center gap-1.5 rounded-lg border border-emerald-900 px-3 py-1.5 text-xs text-emerald-400 hover:bg-emerald-950/30">
                  <CheckCircle2 size={13} /> 入库
                </button>
                <button onClick={() => void onReview('reject')} className="flex items-center gap-1.5 rounded-lg border border-red-900 px-3 py-1.5 text-xs text-red-400 hover:bg-red-950/30">
                  <XCircle size={13} /> 拒收
                </button>
                <button onClick={() => void onReview('deprecate')} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800/40">
                  <Archive size={13} /> 废弃
                </button>
              </div>

              <div className="max-h-[420px] space-y-2 overflow-y-auto">
                {(selected.diffs || []).map((d, i) => (
                  <div key={i} className="rounded-lg border border-slate-800 bg-slate-950/60">
                    <div className="flex items-center gap-2 border-b border-slate-800 px-2 py-1 font-mono text-[10px] text-slate-500">
                      <span>{d.op}</span><span className="flex-1 truncate">{d.path}</span>
                      <span>{d.bytes}B{d.truncated ? ' 已截断' : ''}</span>
                    </div>
                    <pre className="max-h-52 overflow-auto px-2 py-1.5 font-mono text-[11px] leading-relaxed text-slate-400 whitespace-pre-wrap">{d.diff}</pre>
                  </div>
                ))}
                {(selected.pitfalls || []).length > 0 && (
                  <div className="rounded-lg border border-amber-900/50 bg-amber-950/20 px-2 py-1.5">
                    <div className="mb-1 text-[10px] text-amber-500">踩坑（{selected.pitfalls?.length}）</div>
                    {(selected.pitfalls || []).map((p, i) => (
                      <div key={i} className="font-mono text-[10px] text-amber-600/80">{p.symptom} · {p.verified_by}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
