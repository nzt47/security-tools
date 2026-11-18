/**
 * TagsField —— array of string 标签列表（受控）。
 *
 * - 芯片展示 + 输入框回车/逗号添加；
 * - items.enum 存在时额外提供「从可选值添加」下拉（过滤已选）。
 */
import React, { useState } from 'react';
import { Button, Select } from '@/components/ui';
import { FieldLabel } from './FieldLabel';

export interface TagsFieldProps {
  /** 当前标签数组（受控） */
  value: string[];
  /** 值变化回调（始终产出新数组，不原地修改） */
  onChange: (next: string[]) => void;
  /** 可选值（items.enum 展开）；缺省为自由输入 */
  options?: readonly string[];
  /** 字段标题 */
  label?: string;
  /** 字段说明 */
  description?: string;
  /** 必填标记（仅展示星号，不阻断） */
  required?: boolean;
  /** 输入框占位文本 */
  placeholder?: string;
  /** 禁用 */
  disabled?: boolean;
}

export function TagsField({
  value,
  onChange,
  options,
  label,
  description,
  required,
  placeholder = '输入后回车添加',
  disabled,
}: TagsFieldProps) {
  // 仅输入框草稿（展示态）；表单值完全受控于 value/onChange，不产生提交丢失
  const [draft, setDraft] = useState('');
  const tags = Array.isArray(value) ? value : [];

  const addTag = (raw: string) => {
    const tag = raw.trim();
    if (!tag) return;
    setDraft('');
    if (tags.includes(tag)) return; // 去重
    onChange([...tags, tag]);
  };

  const removeTag = (tag: string) => {
    onChange(tags.filter((t) => t !== tag));
  };

  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel title={label} required={required} description={description} />
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-(--border-color) bg-(--bg-elevated) px-2 py-1.5">
        {tags.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-full border border-(--border-hover) bg-(--bg-secondary) px-2 py-0.5 text-xs text-(--text-primary)"
          >
            {tag}
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`移除${tag}`}
              className="text-(--text-muted) hover:text-(--mascot-error)"
              onClick={() => removeTag(tag)}
              disabled={disabled}
            >
              ×
            </Button>
          </span>
        ))}
        <input
          className="min-w-24 flex-1 bg-transparent text-sm text-(--text-primary) placeholder:text-(--text-muted) outline-hidden"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault();
              addTag(draft);
            }
          }}
          placeholder={placeholder}
          disabled={disabled}
          aria-label={label ? `${label}（输入框）` : '标签输入'}
        />
      </div>
      {options && options.length > 0 && (
        <Select
          size="md"
          className="mt-0.5"
          value=""
          disabled={disabled}
          aria-label={label ? `${label}（从可选值添加）` : '从可选值添加'}
          placeholder="＋ 从可选值添加…"
          onChange={(v) => {
            if (v) addTag(v);
          }}
          options={options.filter((o) => !tags.includes(o)).map((o) => ({ label: o, value: o }))}
        />
      )}
    </div>
  );
}
