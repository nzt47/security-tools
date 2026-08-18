/**
 * 新建/编辑卡片表单（任务6）。
 * 受控表单：父组件传 initial（编辑时），提交回调返回 CardInput。
 * 空值提交由父组件负责（可前置校验 insight 等必填）。
 */
import React, { useState } from 'react';
import type { Card, CardInput, CardStatus, CardType } from '../../api/knowledge-types';
import './CardForm.css';

interface CardFormProps {
  /** 编辑模式传入原卡（用于回填）；新建模式省略 */
  initial?: Card;
  /** 提交回调（payload 为创建/更新请求体） */
  onSubmit: (payload: CardInput) => void;
  onCancel: () => void;
  /** 提交中（父组件禁用按钮） */
  submitting?: boolean;
}

const STATUS_OPTIONS: CardStatus[] = ['draft', 'current', 'archive', 'unknown'];
const TYPE_OPTIONS: CardType[] = ['concepts', 'entities', 'insights'];

const CardForm: React.FC<CardFormProps> = ({ initial, onSubmit, onCancel, submitting = false }) => {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [slug, setSlug] = useState(initial?.slug ?? '');
  const [status, setStatus] = useState<CardStatus>(initial?.status ?? 'draft');
  const [type, setType] = useState<CardType>(initial?.type ?? 'concepts');
  const [source, setSource] = useState(initial?.source ?? '');
  const [date, setDate] = useState(initial?.date ?? new Date().toISOString().slice(0, 10));
  const [insight, setInsight] = useState(initial?.insight ?? '');
  const [content, setContent] = useState(initial?.content ?? '');
  const [error, setError] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // 必填校验（与后端 validate_card 对齐的轻量前置）
    if (!title.trim() || !slug.trim() || !insight.trim()) {
      setError('标题 / slug / 核心洞见为必填项');
      return;
    }
    setError('');
    onSubmit({
      title: title.trim(),
      slug: slug.trim(),
      status,
      type,
      source: source.trim(),
      date,
      insight: insight.trim(),
      content,
    });
  };

  return (
    <form className="kb-form" onSubmit={handleSubmit}>
      <div className="kb-form-grid">
        <label className="kb-form-field">
          <span className="kb-form-label">标题 *</span>
          <input
            className="kb-form-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="卡片标题（slug 默认由此生成）"
          />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">slug *</span>
          <input
            className="kb-form-input"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="唯一标识（创建后不可修改）"
          />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">类型</span>
          <select
            className="kb-form-input"
            value={type}
            onChange={(e) => setType(e.target.value as CardType)}
          >
            {TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">状态</span>
          <select
            className="kb-form-input"
            value={status}
            onChange={(e) => setStatus(e.target.value as CardStatus)}
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">来源</span>
          <input
            className="kb-form-input"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="文章 / 播客 / 手动..."
          />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">日期</span>
          <input
            className="kb-form-input"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
      </div>
      <label className="kb-form-field">
        <span className="kb-form-label">核心洞见 *</span>
        <input
          className="kb-form-input"
          value={insight}
          onChange={(e) => setInsight(e.target.value)}
          placeholder="一句话核心洞见（必填）"
        />
      </label>
      <label className="kb-form-field">
        <span className="kb-form-label">正文（支持 [[双链]] 语法）</span>
        <textarea
          className="kb-form-input kb-form-textarea"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={'正文 Markdown，如：\n参考 [[其他卡片slug]] 或 [[其他卡片|别名]]'}
          rows={5}
        />
      </label>

      {error && <div className="kb-form-error">{error}</div>}

      <div className="kb-form-actions">
        <button type="button" className="kb-form-btn kb-form-btn-secondary" onClick={onCancel} disabled={submitting}>
          取消
        </button>
        <button type="submit" className="kb-form-btn kb-form-btn-primary" disabled={submitting}>
          {submitting ? '提交中...' : initial ? '保存' : '创建'}
        </button>
      </div>
    </form>
  );
};

export default CardForm;
