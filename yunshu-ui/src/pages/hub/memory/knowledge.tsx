/**
 * 知识库系统（统一工作台 · 记忆管理/知识库）—— 完整功能版（P1-1 自 legacy /knowledge 迁入）
 * --------------------------------------------------------------------------------
 * 功能：
 *   1) 列表与统计（默认）：只读统计（卡片总数/健康分/图谱节点/图谱连线）+ 状态/类型筛选
 *      的卡片表格（行点击打开详情、行内编辑/删除、新建入口）
 *   2) 融合检索：POST /api/knowledge/query（RRF 融合 + 双链扩展 + rerank 精排，命中可开详情）
 *   3) 健康巡检：GET /api/knowledge/lint（健康分 + 孤儿/死链/索引漂移/超期未访问/建议）
 *   4) 详情抽屉（CardDetail）：frontmatter + 正文 + 出链/入链 + 矛盾标记，双链可跳转
 *   5) 增删改：新建/编辑弹层（CardForm）+ 删除（409 入链保护提示）
 *
 * 数据层：统一使用 src/api/knowledge.ts（底层 lib/apiClient.request + ApiError）——
 *   与 legacy 页同一套 token（authHeader）注入与 JSON 错误体解析（404/409/422/503 可读文本）。
 * 组件：复用 src/components/Knowledge/{StatusBadge,CardDetail,CardForm}（自带深色 kb- 样式），
 *   页面外壳按工作台惯例（p-6 + hub 通用组件，外层 ContentPanel 已提供滚动区）。
 */
import KnowledgeAddPanel from './KnowledgeAddPanel'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { BookOpen, Plus, RefreshCw, Search } from 'lucide-react'
import type { Card, CardDetail, CardInput, HealthReport, KnowledgeHit } from '../../../api/knowledge-types'
import {
  listCards,
  getCard,
  createCard,
  updateCard,
  deleteCard,
  searchKnowledge,
  getLint,
  getGraph,
} from '../../../api/knowledge'
import { ApiError } from '../../../lib/apiClient'
import { Badge, Button, Card as HubCard, DataTable, ErrorBox, Input, Loading, Notice, PageHeader, Select, StatCard, Tabs } from '../components/ui'
import StatusBadge from '../../../components/Knowledge/StatusBadge'
import CardDetailView from '../../../components/Knowledge/CardDetail'
import CardForm from '../../../components/Knowledge/CardForm'

const TYPE_LABEL: Record<string, string> = { concepts: '概念', entities: '实体', insights: '洞见' }
const TYPE_BADGE: Record<string, 'cyan' | 'green' | 'amber'> = {
  concepts: 'cyan',
  entities: 'green',
  insights: 'amber',
}

type TabKey = 'list' | 'search' | 'lint'

const TABS: { key: TabKey; label: string; hint: string }[] = [
  { key: 'list', label: '列表与统计', hint: '卡片 CRUD / 状态与类型筛选' },
  { key: 'search', label: '融合检索', hint: 'RRF 融合 + 双链 + 精排' },
  { key: 'lint', label: '健康巡检', hint: '孤儿 / 死链 / 索引漂移' },
]

/** 统一错误文本提取（ApiError 优先返回后端可读错误） */
function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

const selectCls =
  'rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-300 outline-hidden focus:border-cyan-600'

export default function MemoryKnowledge() {
  // ── 列表与统计 ──
  const [cards, setCards] = useState<Card[]>([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  // ── 图谱统计（只读视图保留 /graph 数据）──
  const [graphInfo, setGraphInfo] = useState<{ nodes: number; edges: number } | null>(null)

  // ── 融合检索 ──
  const [tab, setTab] = useState<TabKey>('list')
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<KnowledgeHit[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  // ── 健康巡检 ──
  const [report, setReport] = useState<HealthReport | null>(null)
  const [lintLoading, setLintLoading] = useState(true)
  const [lintError, setLintError] = useState('')

  // ── 详情抽屉 ──
  const [detailCard, setDetailCard] = useState<CardDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // ── 新建/编辑 ──
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Card | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')

  // ── 操作提示 ──
  const [notice, setNotice] = useState('')

  // ── 数据加载 ──
  const loadCards = useCallback(async () => {
    setLoading(true)
    setListError('')
    try {
      const res = await listCards()
      setCards(res.cards)
    } catch (e) {
      setListError(errText(e))
      setCards([])
    } finally {
      setLoading(false)
    }
  }, [])

  const loadLint = useCallback(async () => {
    setLintLoading(true)
    setLintError('')
    try {
      const res = await getLint()
      setReport(res.report)
    } catch (e) {
      setLintError(errText(e))
      setReport(null)
    } finally {
      setLintLoading(false)
    }
  }, [])

  const loadGraph = useCallback(async () => {
    try {
      const res = await getGraph()
      setGraphInfo({ nodes: res.nodes.length, edges: res.edges.length })
    } catch {
      setGraphInfo(null)
    }
  }, [])

  const reloadAll = useCallback(() => {
    loadCards()
    loadLint()
    loadGraph()
  }, [loadCards, loadLint, loadGraph])

  // 初始化：列表 + 健康报告 + 图谱
  useEffect(() => {
    loadCards()
    loadLint()
    loadGraph()
  }, [loadCards, loadLint, loadGraph])

  // ── 客户端筛选（列表全量拉取后本地过滤，统计始终反映全量）──
  const visibleCards = cards.filter(
    (c) =>
      (!statusFilter || c.status === statusFilter) &&
      (!typeFilter || c.type === typeFilter),
  )

  // ── 融合检索 ──
  const handleSearch = async () => {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    setSearchError('')
    try {
      const res = await searchKnowledge(q, 5)
      setHits(res.hits)
    } catch (e) {
      setSearchError(errText(e))
      setHits([])
    } finally {
      setSearching(false)
    }
  }

  // ── 详情抽屉（竞态守卫：快速切换时丢弃过期响应）──
  const detailSeqRef = useRef(0)
  const openDetail = useCallback(async (slug: string) => {
    const seq = ++detailSeqRef.current
    setDetailLoading(true)
    setDetailCard(null)
    try {
      const res = await getCard(slug)
      if (seq !== detailSeqRef.current) return
      setDetailCard(res.card)
    } catch (e) {
      if (seq !== detailSeqRef.current) return
      setDetailCard(null)
      setListError(errText(e))
    } finally {
      if (seq === detailSeqRef.current) setDetailLoading(false)
    }
  }, [])

  // ── 新建/编辑提交 ──
  const handleSubmitForm = async (payload: CardInput) => {
    setSubmitting(true)
    setFormError('')
    try {
      if (editing) {
        await updateCard(editing.slug, payload)
        setNotice(`已更新卡片「${editing.slug}」`)
      } else {
        await createCard(payload)
        setNotice(`已创建卡片「${payload.slug}」`)
      }
      setFormOpen(false)
      setEditing(null)
      await loadCards()
      loadLint()
    } catch (e) {
      setFormError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  // ── 删除（409 入链保护提示）──
  const handleDelete = async (slug: string) => {
    if (!window.confirm(`确认删除卡片「${slug}」？`)) return
    try {
      await deleteCard(slug)
      setNotice(`已删除卡片「${slug}」`)
      await loadCards()
      loadLint()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.details as { incoming_links?: string[] } | undefined
        const refs = detail?.incoming_links?.join(', ') ?? '未知'
        window.alert(`删除被拒：该卡片存在入链，引用方需先解除引用。引用方: ${refs}`)
      } else {
        setListError(errText(e))
      }
    }
  }

  // ── 状态/类型分布（统计行）──
  const countBy = (fn: (c: Card) => boolean) => cards.filter(fn).length

  const score = report?.health_score
  const scoreColor =
    score === undefined ? 'text-slate-500' : score >= 90 ? 'text-emerald-400' : score >= 70 ? 'text-amber-400' : 'text-red-400'

  return (
    <div className="p-6">
      <PageHeader
        title="知识库系统"
        description="知识卡片管理 · 融合检索 · 健康巡检 · 双链图谱"
        actions={
          <>
            <Button
              variant="primary"
              size="sm"
              onClick={() => { setEditing(null); setFormError(''); setFormOpen(true) }}
            >
              <Plus size={13} /> 新建卡片
            </Button>
            <Button
              type="button"
              variant="default" size="sm"
              onClick={reloadAll}
            >
              <RefreshCw size={12} /> 刷新
            </Button>
          </>
        }
      />

      {notice && (
        <div className="mb-4 rounded-lg border border-cyan-900/60 bg-cyan-950/40 px-4 py-2 text-sm text-cyan-300">
          {notice}
        </div>
      )}

      {/* ── 只读统计行（卡片总数 / 健康分 / 图谱节点 / 图谱连线）── */}
      <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="卡片总数" value={cards.length} icon={<BookOpen size={16} />} />
        <StatCard
          label="健康分"
          value={score === undefined ? '-' : score.toFixed(1)}
          icon={<RefreshCw size={16} />}
          color={scoreColor}
        />
        <StatCard
          label="图谱节点"
          value={graphInfo?.nodes ?? '-'}
          icon={<BookOpen size={16} />}
          color="text-emerald-400"
        />
        <StatCard
          label="图谱连线"
          value={graphInfo?.edges ?? '-'}
          icon={<RefreshCw size={16} />}
          color="text-amber-400"
        />
      </div>

      {/* ── Tab 导航 ── */}
      <div className="mb-4 flex flex-wrap gap-2">
        <Tabs
          variant="pill"
          size="sm"
          value={tab}
          onChange={(v) => setTab(v as typeof tab)}
          items={TABS.map((t) => ({
            value: t.key,
            label: <>{t.key === 'search' ? <Search size={12} /> : null}{t.label}</>,
            title: t.hint,
          }))}
        />
      </div>

      {/* ═══ Tab 1 · 列表与统计（独立加载/错误态）═══ */}
      {tab === 'list' && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Select value={statusFilter} onChange={(v) => setStatusFilter(v)} aria-label="状态筛选" size="sm" placeholder="全部状态" placeholderOptionDisabled={false} options={[{ label: '草稿', value: 'draft' }, { label: '有效', value: 'current' }, { label: '归档', value: 'archive' }, { label: '未知', value: 'unknown' }]} />
            <Select value={typeFilter} onChange={(v) => setTypeFilter(v)} aria-label="类型筛选" size="sm" placeholder="全部类型" placeholderOptionDisabled={false} options={[{ label: '概念', value: 'concepts' }, { label: '实体', value: 'entities' }, { label: '洞见', value: 'insights' }]} />
            <span className="text-xs text-slate-500">
              全库 {cards.length} 张
              {(['concepts', 'entities', 'insights'] as const).map((t) => (
                <span key={t} className="ml-2">
                  {TYPE_LABEL[t]} {countBy((c) => c.type === t)}
                </span>
              ))}
              <span className="ml-2 text-slate-600">当前筛选显示 {visibleCards.length} 张</span>
            </span>
          </div>

          <HubCard title={`知识卡片（${visibleCards.length}/${cards.length}）`}>
            {loading ? (
              <Loading text="卡片列表加载中…" />
            ) : listError ? (
              <ErrorBox message={`卡片列表加载失败：${listError}`} />
            ) : visibleCards.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-sm text-slate-500">
                <BookOpen size={22} />
                <span>暂无卡片（知识库为空或筛选无结果）</span>
              </div>
            ) : (
                  <DataTable<Card>
                    data={visibleCards}
                    keyField="slug"
                    columns={[
                      {
                        key: 'title',
                        title: '标题',
                        render: (r) => (
                          <Button variant="plain" size="bare"
                            type="button"
                            className="max-w-[320px] truncate text-left text-cyan-300 hover:text-cyan-200 hover:underline"
                            title={`打开卡片: ${r.slug}`}
                            onClick={() => openDetail(r.slug)}
                          >
                            {r.title || r.slug}
                          </Button>
                        ),
                      },
                      {
                        key: 'slug',
                        title: 'slug',
                        render: (r) => (
                          <span className="font-mono text-xs text-slate-500">{r.slug}</span>
                        ),
                      },
                      {
                        key: 'type',
                        title: '类型',
                        render: (r) => (
                          <Badge color={TYPE_BADGE[r.type] ?? 'slate'}>
                            {TYPE_LABEL[r.type] ?? r.type}
                          </Badge>
                        ),
                      },
                      {
                        key: 'status',
                        title: '状态',
                        render: (r) => <StatusBadge status={r.status} />,
                      },
                      {
                        key: 'date',
                        title: '日期',
                        render: (r) => (
                          <span className="font-mono text-xs text-slate-500">{r.date || '-'}</span>
                        ),
                      },
                      {
                        key: 'tags',
                        title: '标签',
                        render: (r) => (
                          <div className="flex flex-wrap gap-1">
                            {(r.tags ?? []).slice(0, 4).map((t, i) => (
                              <span
                                key={i}
                                className="rounded-sm bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400"
                              >
                                #{t}
                              </span>
                            ))}
                          </div>
                        ),
                      },
                      {
                        key: 'actions',
                        title: '操作',
                        render: (r) => (
                          <div className="flex items-center gap-1">
                            <Button type="button" title="编辑" onClick={() => { setEditing(r); setFormError(''); setFormOpen(true) }} variant="default" size="xs">
                              ✏️
                            </Button>
                            <Button type="button" title="删除" onClick={() => handleDelete(r.slug)} variant="danger-outline" size="xs">
                              🗑
                            </Button>
                          </div>
                        ),
                      },
                    ]}
                  />
                )}
              </HubCard>
            </>
          )}

          {/* ═══ Tab 2 · 融合检索 ═══ */}
          {tab === 'search' && (
            <HubCard title="融合检索" actions={<span className="text-caption text-slate-500">RRF 融合 + 双链扩展 + 精排</span>}>
              <div className="flex gap-2">
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleSearch() }}
                  placeholder="输入问题，检索知识库（RRF 融合）"
                  size="md"
                  className="flex-1"
                />
                <Button
                  variant="primary"
                  size="md"
                  onClick={handleSearch}
                  disabled={searching || !query.trim()}
                >
                  <Search size={14} /> {searching ? '检索中…' : '检索'}
                </Button>
              </div>

              {searchError && <div className="mt-3"><ErrorBox message={searchError} /></div>}
              {searching && <div className="mt-3"><Loading text="检索中…" /></div>}

              <div className="mt-4 space-y-2">
                {!searching && hits.length === 0 && (
                  <div className="py-8 text-center text-sm text-slate-600">
                    {query.trim() ? '未命中任何卡片' : '输入问题开始融合检索'}
                  </div>
                )}
                {hits.map((h) => (
                  <div key={h.slug} className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5">
                    <Button type="button" onClick={() => openDetail(h.slug)} title={`打开卡片: ${h.slug}`} variant="accent" size="sm">
                      {h.title}
                    </Button>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-slate-500">
                      <StatusBadge status={h.status} />
                      <span className="font-mono text-slate-400">{h.source_ref}</span>
                      <span className="font-mono text-cyan-500">
                        score {h.score.toFixed(3)}
                        {h.rerank_score != null && ` · rerank ${h.rerank_score.toFixed(3)}`}
                      </span>
                    </div>
                    {h.snippet && <p className="mt-1.5 text-xs leading-relaxed text-slate-400">{h.snippet}</p>}
                  </div>
                ))}
              </div>
            </HubCard>
          )}

          {/* ═══ Tab 3 · 健康巡检 ═══ */}
          {tab === 'lint' && (
            <HubCard
              title="健康巡检"
              actions={
                <Button type="button" onClick={loadLint} variant="default" size="xs">
                  <RefreshCw size={11} /> 重新巡检
                </Button>
              }
            >
              {lintLoading ? (
                <Loading text="健康报告加载中…" />
              ) : lintError ? (
                <ErrorBox message={lintError} />
              ) : !report ? (
                <div className="py-8 text-center text-sm text-slate-600">暂无健康报告</div>
              ) : (
                <div>
                  <div className="flex flex-wrap items-end gap-6 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
                    <div>
                      <div className={`text-h1 font-bold ${scoreColor}`}>
                        {report.health_score.toFixed(1)}
                      </div>
                      <div className="mt-1 text-caption text-slate-500">健康分（0-100）</div>
                    </div>
                    <div className="text-xs text-slate-500">
                      共 {report.total_cards} 张卡片 · 巡检于 {report.checked_at}
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
                    {[
                      { label: '孤儿卡片', value: report.orphans.length },
                      { label: '死链', value: report.broken_links.length },
                      { label: '索引漂移', value: report.index_drift.length },
                      { label: '超期未访问', value: report.stale_cards.length },
                    ].map((s) => (
                      <div key={s.label} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-center">
                        <div className="text-xl font-semibold text-slate-100">{s.value}</div>
                        <div className="mt-0.5 text-caption text-slate-500">{s.label}</div>
                      </div>
                    ))}
                  </div>

                  {report.broken_links.length > 0 && (
                    <div className="mt-4">
                      <div className="mb-1 text-xs font-semibold text-slate-400">死链明细</div>
                      <div className="space-y-1 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
                        {report.broken_links.map((b, i) => (
                          <div key={i} className="flex flex-wrap gap-1.5">
                            <span className="text-slate-300">{b.from_slug}</span>
                            <span className="text-slate-600">→</span>
                            <span className="text-red-400">{b.to_slug}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {report.orphans.length > 0 && (
                    <LintSection title={`孤儿卡片（${report.orphans.length} · 无入链）`}>
                      <div className="break-all text-xs leading-relaxed text-slate-400">
                        {report.orphans.join('，')}
                      </div>
                    </LintSection>
                  )}

                  {report.index_drift.length > 0 && (
                    <LintSection title={`索引漂移（${report.index_drift.length} · index.md 不同步）`}>
                      <div className="break-all text-xs leading-relaxed text-slate-400">
                        {report.index_drift.join('，')}
                      </div>
                    </LintSection>
                  )}

                  {report.stale_cards.length > 0 && (
                    <LintSection title={`超期未访问（${report.stale_cards.length}）`}>
                      {report.stale_cards.map((s, i) => (
                        <div key={i} className="text-xs text-slate-400">
                          {s.slug} · {s.days_unaccessed} 天未访问
                        </div>
                      ))}
                    </LintSection>
                  )}

                  {report.unresolved_conflicts.length > 0 && (
                    <LintSection title={`未裁决矛盾（${report.unresolved_conflicts.length}）`}>
                      {report.unresolved_conflicts.map((c, i) => (
                        <div key={i} className="text-xs text-amber-400/90">
                          {c.slug} → {c.target_slug}
                        </div>
                      ))}
                    </LintSection>
                  )}

                  {report.suggestions.length > 0 && (
                    <LintSection title="建议">
                      <ul className="ml-4 list-disc space-y-0.5 text-xs leading-relaxed text-slate-400">
                        {report.suggestions.map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ul>
                    </LintSection>
                  )}

                  {/* 新增知识文档：**只在这里挂一次**。2026-11-18 审计发现它此前挂在 LintSection 里，
                      于是健康巡检页每个小节后面都重复一份（6 份"新增知识文档"）——同一张表单 6 个实例，
                      各自的 useState 互不同步，用户填错地方还以为没保存。 */}
                  <KnowledgeAddPanel />
                </div>
              )}
            </HubCard>
          )}

      {/* ── 详情抽屉（复用 legacy CardDetail 组件）── */}
      {detailLoading && (
        <div className="fixed inset-0 z-1100 flex items-center justify-center">
          <div className="rounded-lg border border-slate-700 bg-slate-900 px-5 py-3 text-sm text-slate-300 shadow-2xl">
            加载详情中…
          </div>
        </div>
      )}
      {detailCard && (
        <CardDetailView
          card={detailCard}
          onOpenLink={openDetail}
          onClose={() => setDetailCard(null)}
        />
      )}

      {/* ── 新建/编辑弹层（表单复用 legacy CardForm 组件）── */}
      {formOpen && (
        <div
          className="fixed inset-0 z-1050 flex items-center justify-center bg-black/50"
          onClick={() => { if (!submitting) { setFormOpen(false); setEditing(null) } }}
        >
          <div
            className="max-h-[88vh] w-[560px] max-w-[94vw] overflow-y-auto rounded-xl border border-slate-700 bg-[#1b1e24] p-5 text-slate-200 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-3 text-title font-semibold text-slate-100">
              {editing ? `编辑卡片: ${editing.slug}` : '新建卡片'}
            </h3>
            <CardForm
              initial={editing ?? undefined}
              onSubmit={handleSubmitForm}
              onCancel={() => { setFormOpen(false); setEditing(null) }}
              submitting={submitting}
            />
            {formError && (
              <Notice tone="danger" className="mt-3">
                {formError}
              </Notice>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** 健康巡检段落小标题容器 */
function LintSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-4">
      <div className="mb-1.5 text-xs font-semibold text-slate-400">{title}</div>
      <div className="space-y-1 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">{children}</div>
    </div>
  )
}
