/**
 * 新建/编辑卡片表单（任务6）。
 * 受控表单：父组件传 initial（编辑时），提交回调返回 CardInput。
 * 空值提交由父组件负责（可前置校验 insight 等必填）。
 */
import React, { useState } from 'react';
import type { Card, CardInput, CardStatus, CardType } from '../../api/knowledge-types';
import './CardForm.css';
import { Button, Input, Select, Textarea } from '@/components/ui';

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
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="卡片标题（slug 默认由此生成）" size="sm" />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">slug *</span>
          <Input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="唯一标识（创建后不可修改）" size="sm" />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">类型</span>
          <Select
            size="sm"
            hidePlaceholder
            value={type}
            onChange={(v) => setType(v as CardType)}
            options={TYPE_OPTIONS.map((t) => ({ label: t, value: t }))}
          />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">状态</span>
          <Select
            size="sm"
            hidePlaceholder
            value={status}
            onChange={(v) => setStatus(v as CardStatus)}
            options={STATUS_OPTIONS.map((s) => ({ label: s, value: s }))}
          />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">来源</span>
          <Input value={source} onChange={(e) => setSource(e.target.value)} placeholder="文章 / 播客 / 手动..." size="sm" />
        </label>
        <label className="kb-form-field">
          <span className="kb-form-label">日期</span>
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} size="sm" />
        </label>
      </div>
      <label className="kb-form-field">
        <span className="kb-form-label">核心洞见 *</span>
        <Input value={insight} onChange={(e) => setInsight(e.target.value)} placeholder="一句话核心洞见（必填）" size="sm" />
      </label>
      <label className="kb-form-field">
        <span className="kb-form-label">正文（支持 [[双链]] 语法）</span>
        <Textarea
          variant="bare"
          className="kb-form-input kb-form-textarea"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={'正文 Markdown，如：\n参考 [[其他卡片slug]] 或 [[其他卡片|别名]]'}
          rows={5}
        />
      </label>

      {error && <div className="kb-form-error">{error}</div>}

      <div className="kb-form-actions">
        <Button type="button" variant="default" size="sm" onClick={onCancel} disabled={submitting}>
          取消
        </Button>
        <Button type="submit" variant="primary" size="sm" disabled={submitting}>
          {submitting ? '提交中...' : initial ? '保存' : '创建'}
        </Button>
      </div>
    </form>
  );
};

export default CardForm;
