/**
 * 新建/编辑卡片表单（任务6 Step 2）
 *
 * 新建：slug 由 title 自动生成（后端 slugify 幂等，前端仅做预览）。
 * 编辑：slug 只读（作为定位标识，不可变更）。
 */
import React, { useState } from 'react';
import type { KnowledgeCard } from '../../api/knowledge';

export interface CardFormProps {
  /** 编辑模式下传入待编辑卡片；新建模式为 null */
  initial?: KnowledgeCard | null;
  onSave: (card: Partial<KnowledgeCard>) => void;
  onCancel: () => void;
  /** 保存失败信息（由父组件透传展示） */
  error?: string | null;
  submitting?: boolean;
}

const TYPE_OPTIONS = ['concepts', 'entities', 'insights'];
const STATUS_OPTIONS = ['draft', 'current', 'archive', 'unknown'];

export const CardForm: React.FC<CardFormProps> = ({
  initial,
  onSave,
  onCancel,
  error,
  submitting,
}) => {
  const [title, setTitle] = useState(initial?.title || '');
  const [slug, setSlug] = useState(initial?.slug || '');
  const [type, setType] = useState(initial?.type || 'concepts');
  const [status, setStatus] = useState(initial?.status || 'draft');
  const [source, setSource] = useState(initial?.source || 'manual');
  const [date, setDate] = useState(initial?.date || new Date().toISOString().slice(0, 10));
  const [tags, setTags] = useState((initial?.tags || []).join(', '));
  const [insight, setInsight] = useState(initial?.insight || '');
  const [content, setContent] = useState(initial?.content || '');

  const isEdit = Boolean(initial);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({
      title,
      slug: slug || title,
      type,
      status,
      source,
      date,
      tags: tags
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean),
      insight,
      content,
    });
  };

  return (
    <form className="kb-form" onSubmit={handleSubmit} data-testid="card-form">
      {error && <div className="kb-form-error">{error}</div>}

      <div className="kb-form-row">
        <label className="kb-form-label">
          标题 <span className="kb-form-required">*</span>
          <input
            className="kb-form-input"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (!isEdit) setSlug(e.target.value);
            }}
            required
            placeholder="卡片标题（slug 自动生成）"
          />
        </label>
      </div>

      <div className="kb-form-row">
        <label className="kb-form-label">
          slug
          <input
            className="kb-form-input"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            readOnly={isEdit}
            disabled={isEdit}
            placeholder="唯一标识"
          />
        </label>
      </div>

      <div className="kb-form-grid">
        <label className="kb-form-label">
          类型
          <select className="kb-form-input" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="kb-form-label">
          状态
          <select className="kb-form-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="kb-form-label">
          来源
          <input
            className="kb-form-input"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
        </label>
        <label className="kb-form-label">
          日期
          <input
            className="kb-form-input"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
      </div>

      <div className="kb-form-row">
        <label className="kb-form-label">
          标签（逗号分隔）
          <input
            className="kb-form-input"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="tag-a, tag-b"
          />
        </label>
      </div>

      <div className="kb-form-row">
        <label className="kb-form-label">
          核心洞见
          <textarea
            className="kb-form-input"
            rows={2}
            value={insight}
            onChange={(e) => setInsight(e.target.value)}
          />
        </label>
      </div>

      <div className="kb-form-row">
        <label className="kb-form-label">
          正文（支持 [[双链]] 语法）
          <textarea
            className="kb-form-input"
            rows={6}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </label>
      </div>

      <div className="kb-form-actions">
        <button className="kb-btn kb-btn-primary" type="submit" disabled={submitting}>
          {submitting ? '保存中...' : '保存'}
        </button>
        <button className="kb-btn" type="button" onClick={onCancel}>
          取消
        </button>
      </div>
    </form>
  );
};

export default CardForm;
