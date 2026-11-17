/**
 * SelectField —— string + enum 下拉选择（受控）。
 *
 * 独立可复用：第三方插件可直接 import 本组件。
 */
import React, { useId } from 'react';
import { Select } from '@/components/ui';
import { FieldLabel } from './FieldLabel';

export interface SelectFieldProps {
  /** 当前值（受控） */
  value: string;
  /** 值变化回调 */
  onChange: (next: string) => void;
  /** 可选项列表 */
  options: readonly string[];
  /** 字段标题 */
  label?: string;
  /** 字段说明 */
  description?: string;
  /** 必填标记（仅展示星号，不阻断） */
  required?: boolean;
  /** 占位项（渲染为 value="" 的 option） */
  placeholder?: string;
  /** 禁用 */
  disabled?: boolean;
}

export function SelectField({
  value,
  onChange,
  options,
  label,
  description,
  required,
  placeholder,
  disabled,
}: SelectFieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel id={id} title={label} required={required} description={description} />
      <Select
        id={id}
        size="md"
        value={value ?? ''}
        onChange={(v) => onChange(v)}
        disabled={disabled}
        aria-required={required || undefined}
        placeholder={placeholder ?? '请选择'}
        hidePlaceholder={!placeholder}
        options={options.map((opt) => ({ label: String(opt), value: String(opt) }))}
      />
    </div>
  );
}
