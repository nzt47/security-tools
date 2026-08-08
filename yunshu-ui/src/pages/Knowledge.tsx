/**
 * 知识库主页（任务6 Step 2）
 *
 * 三区布局：
 *   1. 搜索区：调 /api/knowledge/query，展示融合检索结果（状态角标 + [来源: slug|status]）
 *   2. 列表区：调 /api/knowledge/cards，按类型/状态筛选，点击打开详情
 *   3. 健康区：调 /api/knowledge/lint，展示健康分与问题列表
 * 附带：关系图入口（getGraph 节点-边概览）、新建/编辑卡片（CardForm）、详情抽屉（CardDetail）。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  createCard,
  deleteCard,
  getCard,
  getLint,
  getGraph,
  listCards,
  searchKnowledge,
  updateCard,
  KnowledgeApiError,
  KnowledgeCard,
  KnowledgeHit,
  LintReport,
  GraphNode,
} from '../api/knowledge';
import StatusBadge from '../components/Knowledge/StatusBadge';
import CardDetail from '../components/Knowledge/CardDetail';
import CardForm from '../components/Knowledge/CardForm';
import './Knowledge.css';

const TYPE_OPTIONS = ['', 'concepts', 'entities', 'insights'];
const STATUS_OPTIONS = ['', 'draft', 'current', 'archive', 'unknown'];

const EMPTY_LINT: LintReport = {
  checked_at: '',
  total_cards: 0,
  orphans: [],
  broken_links: [],
  index_drift: [],
  stale_cards: [],
  unresolved_conflicts: [],
  health_score: 100,
  suggestions: [],
};

export const Knowledge: React.FC = () => {
  // ─── 列表区 ───
  const [cards, setCards] = useState<KnowledgeCard[]>([]);
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState('');

  // ─── 详情抽屉 / 表单 ───
  const [detailCard, setDetailCard] = useState<KnowledgeCard | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingCard, setEditingCard] = useState<KnowledgeCard | null>(null);
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // ─── 搜索区 ───
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');

  // ─── 健康区 ───
  const [lint, setLint] = useState<LintReport>(EMPTY_LINT);
  const [lintLoading, setLintLoading] = useState(true);
  const [lintError, setLintError] = useState('');

  // ─── 关系图 ───
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>([]);
  const [graphEdgeCount, setGraphEdgeCount] = useState(0);

  const loadCards = useCallback(async () => {
    setListLoading(true);
    setListError('');
    try {
      const data = await listCards(
        typeFilter || statusFilter
          ? { type: typeFilter || undefined, status: statusFilter || undefined }
          : undefined,
      );
      setCards(data);
    } catch (e) {
      setListError(e instanceof Error ? e.message : '加载列表失败');
    } finally {
      setListLoading(false);
    }
  }, [typeFilter, statusFilter]);

  const loadLint = useCallback(async () => {
    setLintLoading(true);
    setLintError('');
    try {
      setLint(await getLint());
    } catch (e) {
      setLintError(e instanceof Error ? e.message : '健康巡检失败');
    } finally {
      setLintLoading(false);
    }
  }, []);

  const loadGraph = useCallback(async () => {
    try {
      const g = await getGraph();
      setGraphNodes(g.nodes);
      setGraphEdgeCount(g.edges.length);
    } catch {
      /* 关系图失败不阻塞主视图 */
    }
  }, []);

  // ─── 初始化 ───
  useEffect(() => {
    loadCards();
    loadLint();
    loadGraph();
  }, [loadCards, loadLint, loadGraph]);

  // ─── 动作 ───

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchError('');
    try {
      setHits(await searchKnowledge(q, 5));
    } catch (err) {
      setHits([]);
      setSearchError(err instanceof Error ? err.message : '检索失败');
    } finally {
      setSearching(false);
    }
  };

  const openDetail = async (slug: string) => {
    try {
      setDetailCard(await getCard(slug));
    } catch (e) {
      setListError(e instanceof Error ? e.message : '加载详情失败');
    }
  };

  const openCreate = () => {
    setEditingCard(null);
    setFormError('');
    setFormOpen(true);
  };

  const openEdit = (card: KnowledgeCard) => {
    setEditingCard(card);
    setFormError('');
    setFormOpen(true);
    setDetailCard(null);
  };

  const handleSave = async (data: Partial<KnowledgeCard>) => {
    setSubmitting(true);
    setFormError('');
    try {
      if (editingCard) {
        await updateCard(editingCard.slug, data);
      } else {
        await createCard(data as Partial<KnowledgeCard>);
      }
      setFormOpen(false);
      await loadCards();
      await loadLint();
      await loadGraph();
    } catch (err) {
      if (err instanceof KnowledgeApiError && err.body?.violations) {
        setFormError(`${err.message}：${err.body.violations.join('；')}`);
      } else {
        setFormError(err instanceof Error ? err.message : '保存失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (card: KnowledgeCard) => {
    if (!window.confirm(`确认删除卡片「${card.title}」？`)) return;
    try {
      await deleteCard(card.slug);
      setDetailCard(null);
      await loadCards();
      await loadLint();
      await loadGraph();
    } catch (err) {
      if (err instanceof KnowledgeApiError && err.body?.incoming_links) {
        setListError(`删除被拒：存在入链 ${err.body.incoming_links.join(', ')}`);
      } else {
        setListError(err instanceof Error ? err.message : '删除失败');
      }
    }
  };

  const scoreColor =
    lint.health_score >= 90 ? 'kb-score-good' : lint.health_score >= 60 ? 'kb-score-warn' : 'kb-score-bad';

  return (
    <div className="kb-page" data-testid="knowledge-page">
      <div className="kb-header">
        <h2 className="kb-title">知识库</h2>
        <span className="kb-subtitle">卡片管理 · 融合检索 · 健康巡检</span>
      </div>

      {/* 三区网格 */}
      <div className="kb-grid">
        {/* ── 1. 搜索区 ── */}
        <section className="kb-zone kb-zone-search" aria-label="知识库检索">
          <h3 className="kb-zone-title">检索</h3>
          <form className="kb-search-form" onSubmit={handleSearch}>
            <input
              className="kb-search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入问题，融合检索知识库..."
            />
            <button className="kb-btn kb-btn-primary" type="submit" disabled={searching}>
              {searching ? '检索中...' : '检索'}
            </button>
          </form>
          {searchError && <div className="kb-error">{searchError}</div>}
          {hits.length > 0 && (
            <ul className="kb-hit-list">
              {hits.map((h) => (
                <li
                  key={h.slug}
                  className="kb-hit-item"
                  onClick={() => openDetail(h.slug)}
                  role="button"
                  tabIndex={0}
                >
                  <div className="kb-hit-head">
                    <span className="kb-hit-title">{h.title}</span>
                    <StatusBadge status={h.status} />
                  </div>
                  <div className="kb-hit-source">[来源: {h.source_ref} | {h.status}]</div>
                  <p className="kb-hit-snippet">{h.snippet}</p>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── 2. 列表区 ── */}
        <section className="kb-zone kb-zone-list" aria-label="卡片列表">
          <div className="kb-zone-head">
            <h3 className="kb-zone-title">卡片</h3>
            <button className="kb-btn kb-btn-primary kb-btn-sm" onClick={openCreate} type="button">
              + 新建
            </button>
          </div>
          <div className="kb-filters">
            <select
              className="kb-filter"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              aria-label="按类型筛选"
            >
              {TYPE_OPTIONS.map((t) => (
                <option key={t} value={t}>{t || '全部类型'}</option>
              ))}
            </select>
            <select
              className="kb-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="按状态筛选"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s || '全部状态'}</option>
              ))}
            </select>
          </div>
          {listError && <div className="kb-error">{listError}</div>}
          {listLoading ? (
            <div className="kb-empty">加载中...</div>
          ) : cards.length === 0 ? (
            <div className="kb-empty">暂无卡片</div>
          ) : (
            <ul className="kb-card-list">
              {cards.map((c) => (
                <li
                  key={c.slug}
                  className="kb-card-item"
                  onClick={() => openDetail(c.slug)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && openDetail(c.slug)}
                >
                  <div className="kb-card-head">
                    <span className="kb-card-title">{c.title}</span>
                    <StatusBadge status={c.status} />
                  </div>
                  <div className="kb-card-meta">
                    <span className="kb-card-type">{c.type}</span>
                    <span className="kb-card-slug">{c.slug}</span>
                  </div>
                  {c.insight && <p className="kb-card-insight">{c.insight}</p>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── 3. 健康区 ── */}
        <section className="kb-zone kb-zone-health" aria-label="健康报告">
          <div className="kb-zone-head">
            <h3 className="kb-zone-title">健康报告</h3>
            <button className="kb-btn kb-btn-sm" onClick={loadLint} type="button">
              刷新
            </button>
          </div>
          {lintError ? (
            <div className="kb-error">{lintError}</div>
          ) : lintLoading ? (
            <div className="kb-empty">巡检中...</div>
          ) : (
            <div className="kb-lint">
              <div className="kb-score-row">
                <span className={`kb-score ${scoreColor}`} data-testid="health-score">
                  {lint.health_score}
                </span>
                <div className="kb-score-info">
                  <div>共 {lint.total_cards} 张卡片</div>
                  <div className="kb-score-time">巡检于 {lint.checked_at}</div>
                </div>
              </div>

              <ul className="kb-issue-list">
                <li className={`kb-issue ${lint.unresolved_conflicts.length ? 'kb-issue-bad' : ''}`}>
                  未裁决矛盾：{lint.unresolved_conflicts.length}
                </li>
                <li className={`kb-issue ${lint.broken_links.length ? 'kb-issue-bad' : ''}`}>
                  断链：{lint.broken_links.length}
                </li>
                <li className={`kb-issue ${lint.stale_cards.length ? 'kb-issue-warn' : ''}`}>
                  过期声明：{lint.stale_cards.length}
                </li>
                <li className={`kb-issue ${lint.index_drift.length ? 'kb-issue-warn' : ''}`}>
                  index 漂移：{lint.index_drift.length}
                </li>
                <li className={`kb-issue ${lint.orphans.length ? 'kb-issue-warn' : ''}`}>
                  孤儿卡片：{lint.orphans.length}
                </li>
              </ul>

              {lint.suggestions.length > 0 && (
                <div className="kb-suggestions">
                  <h4 className="kb-detail-section-title">建议</h4>
                  <ul>
                    {lint.suggestions.map((s) => (
                      <li key={s} className="kb-suggestion">{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* 关系图入口 */}
          <div className="kb-graph-summary">
            <h4 className="kb-detail-section-title">关系图</h4>
            {graphNodes.length === 0 ? (
              <p className="kb-detail-empty">暂无节点</p>
            ) : (
              <p className="kb-graph-meta">{graphNodes.length} 节点 · {graphEdgeCount} 条引用边</p>
            )}
            <div className="kb-graph-chips">
              {graphNodes.slice(0, 12).map((n) => (
                <span
                  key={n.id}
                  className="kb-graph-chip"
                  onClick={() => openDetail(n.id)}
                  role="button"
                  tabIndex={0}
                  title={n.label}
                >
                  {n.label}
                </span>
              ))}
              {graphNodes.length > 12 && <span className="kb-graph-chip kb-graph-more">+{graphNodes.length - 12}</span>}
            </div>
          </div>
        </section>
      </div>

      {/* 详情抽屉 */}
      {detailCard && (
        <CardDetail
          card={detailCard}
          onClose={() => setDetailCard(null)}
          onEdit={openEdit}
          onDelete={handleDelete}
        />
      )}

      {/* 新建/编辑表单 */}
      {formOpen && (
        <div className="kb-drawer-overlay" onClick={() => setFormOpen(false)}>
          <div
            className="kb-drawer"
            role="dialog"
            aria-label={editingCard ? '编辑卡片' : '新建卡片'}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="kb-drawer-header">
              <h3 className="kb-detail-title">{editingCard ? '编辑卡片' : '新建卡片'}</h3>
              <button className="kb-drawer-close" onClick={() => setFormOpen(false)} type="button" aria-label="关闭">
                ×
              </button>
            </div>
            <div className="kb-drawer-body">
              <CardForm
                initial={editingCard}
                onSave={handleSave}
                onCancel={() => setFormOpen(false)}
                error={formError}
                submitting={submitting}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Knowledge;
