/**
 * ObjectGroup —— 嵌套 object 折叠分组（展示态组件）。
 *
 * 折叠状态属于纯展示 UI 状态，不影响表单值；默认展开由 defaultOpen 控制
 * （SchemaRenderer 约定：第一层嵌套默认展开，更深层默认收起）。
 */
import React, { useState } from 'react';
import { labelClass, descClass } from './FieldLabel';

export interface ObjectGroupProps {
  /** 分组标题 */
  title?: string;
  /** 分组说明 */
  description?: string;
  /** 初始是否展开 */
  defaultOpen?: boolean;
  /** 分组内容（嵌套字段） */
  children: React.ReactNode;
}

export function ObjectGroup({ title, description, defaultOpen = false, children }: ObjectGroupProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div
      className="overflow-hidden rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)]"
      data-testid="object-group"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-[var(--bg-elevated)]"
      >
        <span className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={`inline-block text-xs text-[var(--mascot-primary)] transition-transform ${open ? 'rotate-90' : ''}`}
          >
            ▸
          </span>
          <span className={labelClass}>{title ?? '分组'}</span>
        </span>
        <span className="text-xs text-[var(--text-muted)]">{open ? '收起' : '展开'}</span>
      </button>
      {open && description && <p className={`${descClass} px-3 pb-1`}>{description}</p>}
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}
