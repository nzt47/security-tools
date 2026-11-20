/**
 * SwitchField —— boolean 开关（受控）。
 *
 * 实现：ui/Switch 原语（role="switch" + 键盘可达）；label/description 仍由本字段渲染。
 */
import React from 'react';
import { Switch } from '@/components/ui';
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
    <div className="flex items-center justify-between gap-3">
      <div className="flex flex-col gap-0.5">
        {label && (
          <span className={labelClass}>
            {label}
            {required && (
              <span className="ml-1 text-(--mascot-error)" aria-label="必填">
                *
              </span>
            )}
          </span>
        )}
        {description && <span className={descClass}>{description}</span>}
      </div>
      <Switch
        checked={!!value}
        onCheckedChange={onChange}
        disabled={disabled}
        label={label || '开关'}
      />
    </div>
  );
}
