/**
 * NumberField —— integer/number 数字输入（受控）。
 *
 * min/max 仅透传到原生 input 属性（提供 spinner 与语义约束），
 * 不阻断输入——校验在 SchemaRenderer 提交层提示（见 T3.2 风险注意）。
 */
import React, { useId } from 'react';
import { FieldLabel, controlClass } from './FieldLabel';

export interface NumberFieldProps {
  /** 当前值（受控）；空输入 / 非法输入时为 undefined */
  value: number | undefined;
  /** 值变化回调（非法输入传 undefined，不阻断） */
  onChange: (next: number | undefined) => void;
  /** 数值下限 */
  min?: number;
  /** 数值上限 */
  max?: number;
  /** 步进 */
  step?: number;
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

export function NumberField({
  value,
  onChange,
  min,
  max,
  step,
  label,
  description,
  required,
  placeholder,
  disabled,
}: NumberFieldProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <FieldLabel id={id} title={label} required={required} description={description} />
      <input
        id={id}
        className={controlClass}
        type="number"
        value={value === undefined || Number.isNaN(value) ? '' : value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') {
            onChange(undefined);
            return;
          }
          const n = Number(raw);
          onChange(Number.isNaN(n) ? undefined : n);
        }}
        placeholder={placeholder}
        disabled={disabled}
        aria-required={required || undefined}
      />
    </div>
  );
}
