/**
 * 字段公共样式与标签块（任务 T3.2）。
 *
 * 样式约定：复用 Tailwind 工具类 + styles/theme.css 主题变量（--bg-* / --text-* / --mascot-*），
 * 不引入任何 UI 库。label 与控件通过 htmlFor/id 关联（useId），保证 getByLabelText 可测。
 */
import React from 'react';

/** 通用控件（input/select/textarea）样式 */
export const controlClass =
  'w-full rounded-md border border-[var(--border-color)] bg-[var(--bg-elevated)] px-2.5 py-1.5 ' +
  'text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none ' +
  'transition-colors focus:border-[var(--mascot-primary)] disabled:cursor-not-allowed disabled:opacity-60';

/** 标签样式 */
export const labelClass = 'text-sm font-medium text-[var(--text-primary)]';

/** 说明文字样式 */
export const descClass = 'text-xs text-[var(--text-muted)]';

export interface FieldLabelProps {
  /** 关联控件 id（htmlFor） */
  id?: string;
  /** 标签文本；缺省不渲染 label 元素 */
  title?: string;
  /** 必填星号（红色 *，独立于 label 文本，保证 label 文本可精确匹配） */
  required?: boolean;
  /** 说明文字 */
  description?: string;
}

/** 标签 + 必填星号 + 说明 的公共块 */
export function FieldLabel({ id, title, required, description }: FieldLabelProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-baseline gap-1">
        {title && (
          <label htmlFor={id} className={labelClass}>
            {title}
          </label>
        )}
        {required && (
          <span className="text-sm leading-none text-[var(--mascot-error)]" aria-label="必填">
            *
          </span>
        )}
      </div>
      {description && <p className={descClass}>{description}</p>}
    </div>
  );
}
