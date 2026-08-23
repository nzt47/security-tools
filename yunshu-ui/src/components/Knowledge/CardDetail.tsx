/**
 * 卡片详情抽屉（任务6）：frontmatter 字段 + 正文 + 入链/出链 + 矛盾列表。
 * 纯展示组件：由 Knowledge 页面传入卡片数据与关闭回调。
 */
import React from 'react';
import type { CardDetail, CardStatus } from '@/api/knowledge-types';
import StatusBadge from './StatusBadge';
import './CardDetail.css';

interface CardDetailProps {
  card: CardDetail;
  /** 点击入链/出链目标回调（页面负责跳转详情） */
  onOpenLink?: (slug: string) => void;
  onClose: () => void;
}

/** 类型目录显示名 */
const TYPE_TEXT: Record<string, string> = {
  concepts: '概念',
  entities: '实体',
  insights: '洞见',
};

/** 矛盾状态显示名 */
const CONTRADICTION_TEXT: Record<string, string> = {
  reviewed: '已审阅',
  conflict: '冲突',
  resolved: '已解决',
};

const CardDetail: React.FC<CardDetailProps> = ({ card, onOpenLink, onClose }) => {
  const links = card.links ?? [];
  const incoming = card.incoming_links ?? [];
  const contradictions = card.contradictions ?? [];

  const renderLink = (slug: string) => (
    <button
      key={slug}
      type="button"
      className="kb-link-chip"
      onClick={() => onOpenLink?.(slug)}
      title={`打开卡片: ${slug}`}
    >
      {slug}
    </button>
  );

  return (
    <div className="kb-detail-overlay" onClick={onClose}>
      <div className="kb-detail-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="kb-detail-header">
          <h2 className="kb-detail-title">{card.title}</h2>
          <button type="button" className="kb-detail-close" onClick={onClose} title="关闭">
            ✕
          </button>
        </div>

        <div className="kb-detail-meta">
          <StatusBadge status={card.status as CardStatus} />
          <span className="kb-detail-meta-item">类型: {TYPE_TEXT[card.type] ?? card.type}</span>
          <span className="kb-detail-meta-item">slug: {card.slug}</span>
          <span className="kb-detail-meta-item">来源: {card.source}</span>
          <span className="kb-detail-meta-item">日期: {card.date}</span>
        </div>

        {card.insight && (
          <div className="kb-detail-insight">💡 {card.insight}</div>
        )}

        {card.tags && card.tags.length > 0 && (
          <div className="kb-detail-section">
            <div className="kb-detail-section-title">标签</div>
            <div className="kb-detail-tags">
              {card.tags.map((t) => (
                <span key={t} className="kb-tag">#{t}</span>
              ))}
            </div>
          </div>
        )}

        <div className="kb-detail-section">
          <div className="kb-detail-section-title">正文</div>
          <pre className="kb-detail-content">{card.content || '(空)'}</pre>
        </div>

        <div className="kb-detail-section">
          <div className="kb-detail-section-title">
            出链 ({links.length})
            <span className="kb-detail-hint"> 本卡引用的 [[双链]]</span>
          </div>
          {links.length > 0 ? (
            <div className="kb-link-chips">{links.map(renderLink)}</div>
          ) : (
            <div className="kb-detail-empty">无出链</div>
          )}
        </div>

        <div className="kb-detail-section">
          <div className="kb-detail-section-title">
            入链 ({incoming.length})
            <span className="kb-detail-hint"> 引用本卡的卡片</span>
          </div>
          {incoming.length > 0 ? (
            <div className="kb-link-chips">{incoming.map(renderLink)}</div>
          ) : (
            <div className="kb-detail-empty">无入链</div>
          )}
        </div>

        {contradictions.length > 0 && (
          <div className="kb-detail-section">
            <div className="kb-detail-section-title">矛盾标记 ({contradictions.length})</div>
            <ul className="kb-contradiction-list">
              {contradictions.map((c, i) => (
                <li key={i} className="kb-contradiction-item">
                  <span className="kb-contradiction-target">{c.target_slug}</span>
                  <span className={`kb-contradiction-status kb-cs--${c.status}`}>
                    {CONTRADICTION_TEXT[c.status] ?? c.status}
                  </span>
                  {c.note && <span className="kb-contradiction-note">{c.note}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
};

export default CardDetail;
