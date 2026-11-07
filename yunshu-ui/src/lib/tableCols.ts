/**
 * 表格列宽拖动 —— **三张能力表共用的一份实现**（技能中心 / 工具集 / 工作流）。
 *
 * 【为什么抽出来】列宽拖动最早只做在技能中心（§11.10）：状态 + 鼠标事件 + localStorage
 * 持久化 + 上下限，一共约 30 行。工具集与工作流要"同技能一样自由调列宽"（2026-11-08
 * Operator 要求），若各抄一份，三处必然漂移（宽度下限不一致、双击不恢复、键名不同）；
 * 故状态与拖拽逻辑只在这里，UI 手柄见 `components/ColResizer`。
 *
 * 【口径】
 * - 宽度存 localStorage（键由调用方给，**不共用**：三张表各记各的）；
 * - 双击手柄恢复该列默认值（整表重置会让人误以为"全乱了"）；
 * - 拖动范围 [COL_MIN_WIDTH, COL_MAX_WIDTH]：低于下限列里什么都放不下，高于上限把邻居挤没；
 * - localStorage 不可用（隐私模式 / SSR）时**静默降级**为"本次会话有效"，不抛错、不阻塞渲染。
 */
import { useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'

/** 单列宽度下限（px） */
export const COL_MIN_WIDTH = 80
/** 单列宽度上限（px） */
export const COL_MAX_WIDTH = 720

export type ColWidths = Record<string, number>

export interface ColResizeApi {
  /** 当前各列宽度（px） */
  cols: ColWidths
  /** 生成某列表头的 `onMouseDown` 处理器 */
  startDrag: (key: string) => (e: ReactMouseEvent) => void
  /** 该列恢复默认宽度 */
  reset: (key: string) => void
}

function readWidths(storeKey: string, defaults: ColWidths): ColWidths {
  try {
    const raw = typeof localStorage === 'undefined' ? null : localStorage.getItem(storeKey)
    if (!raw) return defaults
    const parsed = JSON.parse(raw) as Partial<ColWidths> | null
    return parsed ? { ...defaults, ...parsed } : defaults
  } catch {
    return defaults
  }
}

function writeWidths(storeKey: string, widths: ColWidths): void {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(storeKey, JSON.stringify(widths))
  } catch {
    /* 隐私模式 / 配额满：宽度只在本次会话有效，不影响功能 */
  }
}

/**
 * 可拖动列宽。
 *
 * @param defaults 各列默认宽度（必须在渲染期间**稳定**：reset 用它恢复）
 * @param storeKey localStorage 键（每张表一个）
 */
export function useResizableColumns(defaults: ColWidths, storeKey: string): ColResizeApi {
  const [cols, setCols] = useState<ColWidths>(() => readWidths(storeKey, defaults))
  const dragRef = useRef<{ key: string; x: number; w: number } | null>(null)

  const startDrag = (key: string) => (e: ReactMouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragRef.current = { key, x: e.clientX, w: cols[key] ?? defaults[key] ?? COL_MIN_WIDTH }
    const move = (ev: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      const next = Math.max(COL_MIN_WIDTH, Math.min(COL_MAX_WIDTH, d.w + (ev.clientX - d.x)))
      setCols((prev) => ({ ...prev, [d.key]: next }))
    }
    const up = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      dragRef.current = null
      // 松手才落盘：拖动过程中每帧写 localStorage 是白费（一次拖动几十次写）
      setCols((prev) => { writeWidths(storeKey, prev); return prev })
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const reset = (key: string) => setCols((prev) => {
    const next = { ...prev, [key]: defaults[key] ?? COL_MIN_WIDTH }
    writeWidths(storeKey, next)
    return next
  })

  return { cols, startDrag, reset }
}
