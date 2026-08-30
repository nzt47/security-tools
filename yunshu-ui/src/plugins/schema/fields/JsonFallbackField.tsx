/**
 * JsonFallbackField —— 未知类型 / 无法结构化的值降级编辑（受控）。
 *
 * - 内部维护草稿文本（展示态），textarea 始终可编辑；
 * - JSON 合法时才回调 onChange（解析结果）；非法时红字提示且不提交；
 * - 外部 value 变化时与草稿比对，避免格式化打断正在进行的输入。
 */
import React, { useEffect, useRef, useState } from 'react';
import { FieldLabel, controlClass } from './FieldLabel';

export interface JsonFallbackFieldProps {
  /** 当前值（受控） */
  value: unknown;
  /** 值变化回调（仅合法 JSON 触发） */
  onChange: (next: unknown) => void;
  /** 字段标题 */
  label?: string;
  /** 字段说明 */
  description?: string;
  /** 占位文本 */
  placeholder?: string;
  /** 禁用 */
  disabled?: boolean;
}

function toJson(value: unknown): string {
  if (value === undefined || value === null) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function JsonFallbackField({
  value,
  onChange,
  label = 'JSON 值',
  description,
  placeholder = '请输入合法 JSON',
  disabled,
}: JsonFallbackFieldProps) {
  const [draft, setDraft] = useState(() => toJson(value));
  const [error, setError] = useState<string | null>(null);
  const lastEmitted = useRef<string>('');

  // 外部 value 变化：仅当解析结果确实不同才重建草稿（避免打断输入 / 反复格式化）
  useEffect(() => {
    setDraft((prev) => {
      try {
        if (JSON.stringify(JSON.parse(prev)) === JSON.stringify(value)) return prev;
      } catch {
        /* prev 不可解析 → 用新 value 重建 */
      }
      return toJson(value);
    });
    setError(null);
  }, [value]);

  const handleChange = (raw: string) => {
    setDraft(raw);
    const trimmed = raw.trim();
    if (!trimmed) {
      setError(null);
      return;
    }
    try {
      const parsed = JSON.parse(trimmed);
      setError(null);
      lastEmitted.current = raw;
      onChange(parsed);
    } catch (e) {
      setError(e instanceof Error ? `JSON 解析失败：${e.message}` : 'JSON 解析失败');
    }
  };

  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel title={label} required={false} description={description} />
      <textarea
        className={`${controlClass} min-h-32 resize-y font-mono text-xs leading-relaxed`}
        value={draft}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-label={label}
        spellCheck={false}
      />
      {error && (
        <p role="alert" className="text-xs text-[var(--mascot-error)]">
          {error}
        </p>
      )}
    </div>
  );
}
