/**
 * 知识库主页（任务6）：三区布局
 *   1. 搜索区   — /api/knowledge/query 融合检索（含状态角标 + [来源: slug|status]）
 *   2. 列表区   — /api/knowledge/cards 按类型/状态筛选，点击打开详情抽屉
 *   3. 健康区   — /api/knowledge/lint 健康分与问题列表
 *
 * 错误约定：404/409/422 均展示为可读错误文本（ApiError.message）。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type {
  Card,
  CardDetail as KnowledgeCardDetail,
  CardInput,
  CardStatus,
  CardType,
  HealthReport,
  KnowledgeHit,
} from '@/api/knowledge-types';
import {
  listCards,
  getCard,
  createCard,
  updateCard,
  deleteCard,
  searchKnowledge,
  getLint,
} from '@/api/knowledge';
import { ApiError } from '@/lib/apiClient';
import StatusBadge from '@/components/Knowledge/StatusBadge';
import CardDetail from '@/components/Knowledge/CardDetail';
import CardForm from '@/components/Knowledge/CardForm';
import './Knowledge.css';

const TYPE_LABEL: Record<string, string> = { concepts: '概念', entities: '实体', insights: '洞见' };

const Knowledge: React.FC = () => {
  // ── 列表区 ──
  const [cards, setCards] = useState<Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('');

  // ── 搜索区 ──
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const searchedRef = useRef(false);

  // ── 健康区 ──
  const [report, setReport] = useState<HealthReport | null>(null);
  const [lintError, setLintError] = useState('');

  // ── 详情抽屉 ──
  const [detailCard, setDetailCard] = useState<KnowledgeCardDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // ── 新建/编辑表单 ──
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Card | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  // ── 数据加载 ──
  const loadCards = useCallback(async (status?: string, type?: string) => {
    setLoading(true);
    setListError('');
    try {
      const res = await listCards({ status: status as CardStatus, type: type as CardType });
      setCards(res.cards);
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
      setCards([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLint = useCallback(async () => {
    setLintError('');
    try {
      const res = await getLint();
      setReport(res.report);
    } catch (e) {
      setLintError(e instanceof Error ? e.message : String(e));
      setReport(null);
    }
  }, []);

  // 初始化：列表 + 健康报告
  useEffect(() => {
    (async () => {
      try {
        await loadCards();
        loadLint();
      } catch (e) {
        setListError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      }
    })();
  }, [loadCards, loadLint]);

  // 筛选变化 → 重新加载
  useEffect(() => {
    if (!loading || cards.length > 0 || listError) {
      loadCards(statusFilter, typeFilter);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, typeFilter]);

  // ── 搜索 ──
  const handleSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchError('');
    try {
      const res = await searchKnowledge(q, 5);
      setHits(res.hits);
      searchedRef.current = true;
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e));
      setHits([]);
    } finally {
      setSearching(false);
    }
  };

  const handleQueryKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch();
  };

  // ── 详情抽屉 ──
  // 竞态守卫：快速切换卡片时，仅接受最新一次请求的响应（过期响应丢弃）
  const detailSeqRef = useRef(0);
  const openDetail = async (slug: string) => {
    const seq = ++detailSeqRef.current;
    setDetailLoading(true);
    setDetailCard(null);
    try {
      const res = await getCard(slug);
      if (seq !== detailSeqRef.current) return; // 已有更新的请求，丢弃过期响应
      setDetailCard(res.card);
    } catch (e) {
      if (seq !== detailSeqRef.current) return;
      setDetailCard(null);
      setListError(e instanceof Error ? e.message : String(e));
    } finally {
      if (seq === detailSeqRef.current) setDetailLoading(false);
    }
  };

  // ── 新建/编辑 ──
  const handleSubmitForm = async (payload: CardInput) => {
    setSubmitting(true);
    setFormError('');
    try {
      if (editing) {
        await updateCard(editing.slug, payload);
      } else {
        await createCard(payload);
      }
      setFormOpen(false);
      setEditing(null);
      await loadCards(statusFilter, typeFilter);
      loadLint();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  // ── 删除 ──
  const handleDelete = async (slug: string) => {
    if (!window.confirm(`确认删除卡片「${slug}」？`)) return;
    try {
      await deleteCard(slug);
      await loadCards(statusFilter, typeFilter);
      loadLint();
    } catch (e) {
      // 409 入链保护：提示引用方
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.details as { incoming_links?: string[] } | undefined;
        const refs = detail?.incoming_links?.join(', ') ?? '未知';
        window.alert(`删除被拒：该卡片存在入链，引用方需先解除引用。引用方: ${refs}`);
      } else {
        setListError(e instanceof Error ? e.message : String(e));
      }
    }
  };

  return (
    <div className="kb-page">
      <header className="kb-header">
        <h2 className="kb-title">知识库</h2>
        <button type="button" className="kb-new-btn" onClick={() => { setEditing(null); setFormOpen(true); }}>
          ✚ 新建卡片
        </button>
      </header>

      <div className="kb-layout">
        {/* ── 搜索区 ── */}
        <section className="kb-panel kb-search-panel">
          <h3 className="kb-panel-title">融合检索</h3>
          <div className="kb-search-row">
            <input
              className="kb-search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleQueryKeyDown}
              placeholder="输入问题，检索知识库（RRF 融合）"
            />
            <button
              type="button"
              className="kb-search-btn"
              onClick={handleSearch}
              disabled={searching || !query.trim()}
            >
              {searching ? '检索中...' : '检索'}
            </button>
          </div>
          {searchError && <div className="kb-error-text">{searchError}</div>}
          {hits.length > 0 && (
            <ul className="kb-hit-list">
              {hits.map((h) => (
                <li key={h.slug} className="kb-hit-item">
                  <button type="button" className="kb-hit-title" onClick={() => openDetail(h.slug)}>
                    {h.title}
                  </button>
                  <div className="kb-hit-meta">
                    <StatusBadge status={h.status} />
                    <span className="kb-hit-source">{h.source_ref}</span>
                    <span className="kb-hit-score">score {h.score.toFixed(3)}</span>
                  </div>
                  {h.snippet && <p className="kb-hit-snippet">{h.snippet}</p>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── 列表区 ── */}
        <section className="kb-panel kb-list-panel">
          <h3 className="kb-panel-title">卡片列表 ({cards.length})</h3>
          <div className="kb-filter-row">
            <select
              className="kb-filter-select"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">全部状态</option>
              <option value="draft">草稿</option>
              <option value="current">有效</option>
              <option value="archive">归档</option>
              <option value="unknown">未知</option>
            </select>
            <select
              className="kb-filter-select"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">全部类型</option>
              <option value="concepts">概念</option>
              <option value="entities">实体</option>
              <option value="insights">洞见</option>
            </select>
          </div>

          {listError && <div className="kb-error-text">{listError}</div>}
          {loading ? (
            <div className="kb-loading">加载中...</div>
          ) : cards.length === 0 ? (
            <div className="kb-loading">暂无卡片（知识库为空或筛选无结果）</div>
          ) : (
            <ul className="kb-card-list">
              {cards.map((c) => (
                <li key={c.slug} className="kb-card-item">
                  <button type="button" className="kb-card-main" onClick={() => openDetail(c.slug)}>
                    <span className="kb-card-title">{c.title}</span>
                    <span className="kb-card-slug">{c.slug}</span>
                    <span className="kb-card-type">{TYPE_LABEL[c.type] ?? c.type}</span>
                  </button>
                  <div className="kb-card-actions">
                    <StatusBadge status={c.status} />
                    <button
                      type="button"
                      className="kb-icon-btn"
                      onClick={() => { setEditing(c); setFormOpen(true); }}
                      title="编辑"
                    >
                      ✏️
                    </button>
                    <button
                      type="button"
                      className="kb-icon-btn kb-icon-danger"
                      onClick={() => handleDelete(c.slug)}
                      title="删除"
                    >
                      🗑
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── 健康区 ── */}
        <section className="kb-panel kb-health-panel">
          <h3 className="kb-panel-title">健康巡检</h3>
          {lintError && <div className="kb-error-text">{lintError}</div>}
          {report ? (
            <div className="kb-health-body">
              <div className={`kb-health-score ${report.health_score >= 90 ? 'kb-score-good' : report.health_score >= 70 ? 'kb-score-mid' : 'kb-score-bad'}`}>
                {report.health_score.toFixed(1)}
                <span className="kb-health-score-label">健康分</span>
              </div>
              <div className="kb-health-meta">
                共 {report.total_cards} 张卡片 · 巡检于 {report.checked_at}
              </div>
              <div className="kb-health-grid">
                <div className="kb-health-stat">
                  <span className="kb-health-num">{report.orphans.length}</span> 孤儿卡片
                </div>
                <div className="kb-health-stat">
                  <span className="kb-health-num">{report.broken_links.length}</span> 死链
                </div>
                <div className="kb-health-stat">
                  <span className="kb-health-num">{report.index_drift.length}</span> 索引漂移
                </div>
                <div className="kb-health-stat">
                  <span className="kb-health-num">{report.stale_cards.length}</span> 超期未访问
                </div>
              </div>
              {report.broken_links.length > 0 && (
                <div className="kb-health-section">
                  <div className="kb-health-section-title">死链明细</div>
                  {report.broken_links.map((b, i) => (
                    <div key={i} className="kb-health-line">
                      {b.from_slug} → <span className="kb-health-broken">{b.to_slug}</span>
                    </div>
                  ))}
                </div>
              )}
              {report.orphans.length > 0 && (
                <div className="kb-health-section">
                  <div className="kb-health-section-title">孤儿卡片（无入链）</div>
                  <div className="kb-health-line">{report.orphans.join(', ')}</div>
                </div>
              )}
              {report.suggestions.length > 0 && (
                <div className="kb-health-section">
                  <div className="kb-health-section-title">建议</div>
                  <ul className="kb-suggest-list">
                    {report.suggestions.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="kb-loading">健康报告加载中...</div>
          )}
        </section>
      </div>

      {/* ── 详情抽屉 ── */}
      {detailLoading && <div className="kb-loading kb-loading-fixed">加载详情中...</div>}
      {detailCard && (
        <CardDetail
          card={detailCard}
          onOpenLink={openDetail}
          onClose={() => setDetailCard(null)}
        />
      )}

      {/* ── 新建/编辑表单弹层 ── */}
      {formOpen && (
        <div className="kb-modal-overlay" onClick={() => { if (!submitting) { setFormOpen(false); setEditing(null); } }}>
          <div className="kb-modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="kb-modal-title">{editing ? `编辑卡片: ${editing.slug}` : '新建卡片'}</h3>
            <CardForm
              initial={editing ?? undefined}
              onSubmit={handleSubmitForm}
              onCancel={() => { setFormOpen(false); setEditing(null); }}
              submitting={submitting}
            />
            {formError && <div className="kb-form-error">{formError}</div>}
          </div>
        </div>
      )}
    </div>
  );
};

export default Knowledge;
