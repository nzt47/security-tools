/**
 * TextareaField —— string + format:'textarea' 多行输入（受控）。
 *
 * 独立可复用：第三方插件可直接 import 本组件。
 */
import React, { useId } from 'react';
import { FieldLabel, controlClass } from './FieldLabel';

export interface TextareaFieldProps {
  /** 当前值（受控） */
  value: string;
  /** 值变化回调 */
  onChange: (next: string) => void;
  /** 字段标题 */
  label?: string;
  /** 字段说明 */
  description?: string;
  /** 必填标记（仅展示星号，不阻断） */
  required?: boolean;
  /** 占位文本 */
  placeholder?: string;
  /** 行数 */
  rows?: number;
  /** 禁用 */
  disabled?: boolean;
}

export function TextareaField({
  value,
  onChange,
  label,
  description,
  required,
  placeholder,
  rows = 3,
  disabled,
}: TextareaFieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel id={id} title={label} required={required} description={description} />
      <textarea
        id={id}
        className={`${controlClass} min-h-16 resize-y leading-relaxed`}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
        aria-required={required || undefined}
      />
    </div>
  );
}
