/**
 * SwitchField —— boolean 开关（受控）。
 *
 * 实现：label 包裹隐藏 checkbox（sr-only）+ 轨道/滑块；role="switch" 保证可测性与可访问性。
 */
import React from 'react';
import { labelClass, descClass } from './FieldLabel';

export interface SwitchFieldProps {
  /** 当前值（受控） */
  value: boolean;
  /** 值变化回调 */
  onChange: (next: boolean) => void;
  /** 字段标题 */
  label?: string;
  /** 字段说明 */
  description?: string;
  /** 必填标记（仅展示星号，不阻断） */
  required?: boolean;
  /** 禁用 */
  disabled?: boolean;
}

export function SwitchField({
  value,
  onChange,
  label,
  description,
  required,
  disabled,
}: SwitchFieldProps) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3">
      <div className="flex flex-col gap-0.5">
        {label && (
          <span className={labelClass}>
            {label}
            {required && (
              <span className="ml-1 text-[var(--mascot-error)]" aria-label="必填">
                *
              </span>
            )}
          </span>
        )}
        {description && <span className={descClass}>{description}</span>}
      </div>
      <input
        type="checkbox"
        role="switch"
        className="sr-only"
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
      <span
        aria-hidden="true"
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors ${
          value
            ? 'border-[var(--mascot-primary)] bg-[var(--mascot-primary)]'
            : 'border-[var(--border-color)] bg-[var(--bg-elevated)]'
        }`}
      >
        <span
          className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
            value ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </span>
    </label>
  );
}
