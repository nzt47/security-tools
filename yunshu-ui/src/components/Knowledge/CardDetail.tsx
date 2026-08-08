/**
 * 卡片详情抽屉（任务6 Step 2）
 *
 * 展示 frontmatter 字段、正文、入链/出链、矛盾列表；支持编辑与删除动作。
 */
import React from 'react';
import type { KnowledgeCard } from '../../api/knowledge';
import StatusBadge from './StatusBadge';

export interface CardDetailProps {
  card: KnowledgeCard;
  onClose: () => void;
  onEdit: (card: KnowledgeCard) => void;
  onDelete: (card: KnowledgeCard) => void;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="kb-detail-field">
      <span className="kb-detail-field-label">{label}</span>
      <div className="kb-detail-field-value">{children}</div>
    </div>
  );
}

export const CardDetail: React.FC<CardDetailProps> = ({ card, onClose, onEdit, onDelete }) => {
  const outLinks = card.links || [];
  const inLinks = card.incoming_links || [];
  const contradictions = card.contradictions || [];

  return (
    <div className="kb-drawer-overlay" onClick={onClose} data-testid="card-detail">
      <aside
        className="kb-drawer"
        role="dialog"
        aria-label="卡片详情"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kb-drawer-header">
          <div className="kb-drawer-title">
            <h3 className="kb-detail-title">{card.title}</h3>
            <StatusBadge status={card.status} size="md" />
          </div>
          <button className="kb-drawer-close" onClick={onClose} type="button" aria-label="关闭">
            ×
          </button>
        </div>

        <div className="kb-drawer-body">
          {/* frontmatter 字段 */}
          <div className="kb-detail-fields">
            <Field label="slug">{card.slug}</Field>
            <Field label="类型">{card.type}</Field>
            <Field label="来源">{card.source}</Field>
            <Field label="日期">{card.date}</Field>
            {card.tags && card.tags.length > 0 && (
              <Field label="标签">
                {card.tags.map((t) => (
                  <span key={t} className="kb-tag">{t}</span>
                ))}
              </Field>
            )}
            {card.insight && (
              <Field label="核心洞见">
                <p className="kb-detail-insight">{card.insight}</p>
              </Field>
            )}
          </div>

          {/* 正文 */}
          {card.content && (
            <div className="kb-detail-section">
              <h4 className="kb-detail-section-title">正文</h4>
              <pre className="kb-detail-content">{card.content}</pre>
            </div>
          )}

          {/* 出链 / 入链 */}
          <div className="kb-detail-section kb-detail-links">
            <div>
              <h4 className="kb-detail-section-title">出链 ({outLinks.length})</h4>
              {outLinks.length === 0 ? (
                <p className="kb-detail-empty">无</p>
              ) : (
                <ul className="kb-link-list">
                  {outLinks.map((l) => (
                    <li key={l} className="kb-link-item">{l}</li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h4 className="kb-detail-section-title">入链 ({inLinks.length})</h4>
              {inLinks.length === 0 ? (
                <p className="kb-detail-empty">无</p>
              ) : (
                <ul className="kb-link-list">
                  {inLinks.map((l) => (
                    <li key={l} className="kb-link-item">{l}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* 矛盾列表 */}
          {contradictions.length > 0 && (
            <div className="kb-detail-section">
              <h4 className="kb-detail-section-title">矛盾 ({contradictions.length})</h4>
              <ul className="kb-contradiction-list">
                {contradictions.map((c, i) => (
                  <li key={`${c.target_slug}-${i}`} className="kb-contradiction-item">
                    <StatusBadge status={c.status === 'resolved' ? 'archive' : c.status === 'conflict' ? 'unknown' : 'current'} />
                    <span className="kb-contradiction-target">{c.target_slug}</span>
                    {c.summary && <span className="kb-contradiction-summary">{c.summary}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="kb-drawer-footer">
          <button
            className="kb-btn kb-btn-primary"
            onClick={() => onEdit(card)}
            type="button"
          >
            编辑
          </button>
          <button
            className="kb-btn kb-btn-danger"
            onClick={() => onDelete(card)}
            type="button"
          >
            删除
          </button>
        </div>
      </aside>
    </div>
  );
};

export default CardDetail;
