/**
 * InputField —— string 单行输入（受控）。
 *
 * 独立可复用：第三方插件可直接 import 本组件。
 */
import React, { useId } from 'react';
import { FieldLabel, controlClass } from './FieldLabel';

export interface InputFieldProps {
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
  /** 禁用 */
  disabled?: boolean;
}

export function InputField({
  value,
  onChange,
  label,
  description,
  required,
  placeholder,
  disabled,
}: InputFieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel id={id} title={label} required={required} description={description} />
      <input
        id={id}
        className={controlClass}
        type="text"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-required={required || undefined}
      />
    </div>
  );
}
