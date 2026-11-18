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
import LearningMetrics from './LearningMetrics'
import { useCallback, useEffect, useState } from 'react'
import { Search, RefreshCw, Undo2, CheckCircle2, XCircle, Archive } from 'lucide-react'
import { Badge, Button, Card, ErrorBox, Input, Loading, Notice, PageHeader, RowButton, Select, StatCard } from '../components/ui'
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
          <Button onClick={() => void load()} variant="accent" size="xs">
            <RefreshCw size={13} /> 刷新
          </Button>
        }
      />

      {/* 方案要求：面板顶部固定提示 */}
      <div data-testid="experience-notice" className="mb-4 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-400">
        {NOTICE}
      </div>

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <Notice tone="success" size="md" className="mb-4">{msg}</Notice>}

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
          <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void onSearch()} placeholder="输入一个真实问题，看能否命中…" aria-label="检索预览输入" size="md" className="flex-1" />
          <Button variant="primary" size="md" onClick={() => void onSearch()}>
            <Search size={14} /> 预览
          </Button>
        </div>
        {hits.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {hits.map((h) => (
              <div key={h.id} className="flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                <span className="font-mono text-xs text-cyan-500">{h.score.toFixed(4)}</span>
                <span className="flex-1 text-xs text-slate-300">{h.task}</span>
                <Badge color={VERIFIED_COLOR[h.verified || ''] || 'slate'}>{h.verified || '?'}</Badge>
                <span className="text-micro text-slate-600">{h.legs.join('+')}</span>
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
          <Input value={batchId} onChange={(e) => setBatchId(e.target.value)} placeholder="batch_id" aria-label="批次 ID" size="md" className="flex-1" inputClassName="font-mono" />
          <Button onClick={() => void onRollback()} disabled={!batchId.trim()} variant="danger-outline" size="sm">
            <Undo2 size={14} /> 回滚
          </Button>
        </div>
        <div className="mt-1.5 text-micro text-slate-600">回滚会写审计链留痕，batch_id 即回滚凭据。</div>
      </Card>

      {/* 列表 + 详情 */}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-300">样本（{total}）</span>
            <Select value={lang} onChange={(v) => setLang(v)} aria-label="语言筛选" size="sm" placeholder="全部语言" placeholderOptionDisabled={false} options={[{ label: 'python', value: 'python' }, { label: 'typescript', value: 'typescript' }, { label: 'markdown', value: 'markdown' }, { label: 'yaml', value: 'yaml' }]} />
            <Select value={status} onChange={(v) => setStatus(v)} aria-label="状态筛选" size="sm" placeholder="全部状态" placeholderOptionDisabled={false} options={[{ label: '待审', value: 'pending' }, { label: '已入库', value: 'accept' }, { label: '已拒收', value: 'reject' }, { label: '已废弃', value: 'deprecate' }]} />
          </div>
          {loading && <Loading />}
          <div className="max-h-[460px] space-y-1.5 overflow-y-auto">
            {items.map((it) => (
              <RowButton key={it.id} selected={selected?.id === it.id} onClick={() => void onOpen(it.id)}
                className="rounded-lg border border-border px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="flex-1 truncate text-xs text-slate-300">{it.task || it.id}</span>
                  <Badge color={VERIFIED_COLOR[it.verified || ''] || 'slate'}>{it.verified || '?'}</Badge>
                </div>
                <div className="mt-1 flex gap-2 font-mono text-micro text-slate-600">
                  <span>{it.task_type}</span>
                  <span>{it.stack?.lang}</span>
                  <span>diff×{it.diffs?.length ?? 0}</span>
                  <span>pitfall×{it.pitfalls?.length ?? 0}</span>
                  <span>{it.review_status || 'pending'}</span>
                </div>
              </RowButton>
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
                <Button onClick={() => void onReview('accept')} variant="success-outline" size="xs">
                  <CheckCircle2 size={13} /> 入库
                </Button>
                <Button onClick={() => void onReview('reject')} variant="danger-outline" size="xs">
                  <XCircle size={13} /> 拒收
                </Button>
                <Button onClick={() => void onReview('deprecate')} variant="default" size="xs">
                  <Archive size={13} /> 废弃
                </Button>
              </div>

              <div className="max-h-[420px] space-y-2 overflow-y-auto">
                {(selected.diffs || []).map((d, i) => (
                  <div key={i} className="rounded-lg border border-slate-800 bg-slate-950/60">
                    <div className="flex items-center gap-2 border-b border-slate-800 px-2 py-1 font-mono text-micro text-slate-500">
                      <span>{d.op}</span><span className="flex-1 truncate">{d.path}</span>
                      <span>{d.bytes}B{d.truncated ? ' 已截断' : ''}</span>
                    </div>
                    <pre className="max-h-52 overflow-auto px-2 py-1.5 font-mono text-caption leading-relaxed text-slate-400 whitespace-pre-wrap">{d.diff}</pre>
                  </div>
                ))}
                {(selected.pitfalls || []).length > 0 && (
                  <div className="rounded-lg border border-amber-900/50 bg-amber-950/20 px-2 py-1.5">
                    <div className="mb-1 text-micro text-amber-500">踩坑（{selected.pitfalls?.length}）</div>
                    {(selected.pitfalls || []).map((p, i) => (
                      <div key={i} className="font-mono text-micro text-amber-600/80">{p.symptom} · {p.verified_by}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Card>
      </div>
      <LearningMetrics />
    </div>
  )
}
