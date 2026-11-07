/**
 * 列宽拖动手柄 —— 三张能力表（技能中心 / 工具集 / 工作流）表头里的唯一形态。
 *
 * 只画手柄、不持有状态：状态与拖拽逻辑在 `lib/tableCols.useResizableColumns`，
 * 这样「同技能一样调列宽」在两张新表上是**同一份实现**，不会各写一版。
 */
import type { MouseEvent as ReactMouseEvent } from 'react'

/** 手柄的可访问名与提示（playwright / 单测都按这个找） */
export const COL_RESIZER_LABEL = '拖动调整列宽'
export const COL_RESIZER_TITLE = '拖动调整列宽（双击恢复默认）'

export default function ColResizer({
  onStart, onReset, label = COL_RESIZER_LABEL,
}: {
  /** 按下手柄：开始拖（由 useResizableColumns.startDrag 提供） */
  onStart: (e: ReactMouseEvent) => void
  /** 双击手柄：该列恢复默认宽度 */
  onReset: () => void
  label?: string
}) {
  return (
    <span
      role="separator"
      aria-label={label}
      title={COL_RESIZER_TITLE}
      onMouseDown={onStart}
      onDoubleClick={onReset}
      className="ml-1 inline-block h-3 w-1 cursor-col-resize rounded-sm bg-slate-700 align-middle hover:bg-cyan-500"
    />
  )
}
