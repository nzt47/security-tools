/**
 * 提示词实验室 · 三列宽度（可拖拽 + 自动保存）
 * ------------------------------------------------------------------
 * 【为什么有这个文件】2026-11-15 Owner：「改为列宽可拖拽并自动保存，由我来定列宽吧。」
 * 之前宽度是写死的（先是"剩余宽的 1/3"、后是固定 300px）—— 写死多少都只能对一种屏幕
 * 和一种读法合适：窄了滑块的数值被切，宽了正文列被挤。改成**拖拽 + 记住**：
 * 默认 300 / 440，Owner 自己拖，松手即写 localStorage，下次进来还是他调好的那份。
 *
 * 布局模型：三列 = 因素列（固定 px）+ ① 分节配置列（固定 px）+ ② 最终注入文本（吃剩余）。
 * 两条 24px 拖拽条夹在中间；拖哪条只改它左边那一列的宽度，右边两列的关系不变
 * （② 是 flex:1，所以它总是自动吸收剩余 —— 与 mosaic 分割条的手感一致）。
 *
 * 下限是硬约束：因素列 220 / 分节列 300 / ② 至少 360。窗口变小（resize）时按同一套
 * 下限重新 clamp，不写盘（那只是"当前装不下"，不是 Owner 的新偏好）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export type ColumnHandle = 'factors' | 'identity'

export interface ColumnWidths {
  factors: number
  identity: number
}

/** localStorage 键（与 store 的 yunshu:prompt-lab:v1、Tab 的 yunshu.prompt-lab.tab 并列） */
export const PL_COLUMNS_STORAGE_KEY = 'yunshu:prompt-lab:cols:v1'

/** 首次进入的默认宽度（px）：因素列够放滑块 + 数值，分节列够放卡片与发出内容 */
export const DEFAULT_COLUMNS: ColumnWidths = { factors: 300, identity: 440 }

/** 三列下限（px）：再窄滑块/数值、卡片、正文都会被挤坏 */
export const MIN_COLUMNS = { factors: 220, identity: 300, preview: 360 } as const

/** 两条拖拽条的宽度合计（clamp 时要从可用宽度里扣掉） */
export const SPLITTER_TOTAL = 24

const clamp = (v: number, min: number, max: number): number => Math.min(Math.max(v, min), max)

/** 可用宽度 = body 的内容宽（clientWidth 含 padding，要减掉左右各 20） */
function contentWidth(el: HTMLElement | null): number {
  if (!el) return 0
  const cs = getComputedStyle(el)
  const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0)
  return Math.max(0, el.clientWidth - pad)
}

/**
 * 拖拽结果：只改被拖那一列的宽度，并保证 ② 至少还能留下 MIN_COLUMNS.preview。
 *
 * @param start     拖拽开始时的两列宽度（**不是**当前状态，否则会累加跳跃）
 * @param handle    拖的是哪条
 * @param dx        相对拖拽起点的位移（px）
 * @param available 三列可用宽度（= 内容宽 - 两条拖拽条）
 */
export function resizeColumns(
  start: ColumnWidths,
  handle: ColumnHandle,
  dx: number,
  available: number,
): ColumnWidths {
  const room = Math.max(0, available - SPLITTER_TOTAL)
  if (handle === 'factors') {
    const max = Math.max(MIN_COLUMNS.factors, room - start.identity - MIN_COLUMNS.preview)
    return { ...start, factors: clamp(start.factors + dx, MIN_COLUMNS.factors, max) }
  }
  const max = Math.max(MIN_COLUMNS.identity, room - start.factors - MIN_COLUMNS.preview)
  return { factors: start.factors, identity: clamp(start.identity + dx, MIN_COLUMNS.identity, max) }
}

/** 读存档；脏数据/缺字段一律回默认（不抛错、不白屏） */
export function readColumns(): ColumnWidths {
  try {
    const raw = localStorage.getItem(PL_COLUMNS_STORAGE_KEY)
    if (!raw) return { ...DEFAULT_COLUMNS }
    const d = JSON.parse(raw) as Partial<ColumnWidths> | null
    if (!d || typeof d !== 'object') return { ...DEFAULT_COLUMNS }
    const ok = (v: unknown, min: number): v is number =>
      typeof v === 'number' && Number.isFinite(v) && v >= min
    return {
      factors: ok(d.factors, MIN_COLUMNS.factors) ? d.factors : DEFAULT_COLUMNS.factors,
      identity: ok(d.identity, MIN_COLUMNS.identity) ? d.identity : DEFAULT_COLUMNS.identity,
    }
  } catch {
    return { ...DEFAULT_COLUMNS }
  }
}

/** 写存档（松手/复位时调用；localStorage 不可用就仅本次会话生效） */
export function saveColumns(w: ColumnWidths): void {
  try {
    localStorage.setItem(PL_COLUMNS_STORAGE_KEY, JSON.stringify(w))
  } catch {
    /* 隐私模式/配额满：不影响本次使用 */
  }
}

export interface ColumnLayout {
  cols: ColumnWidths
  /** 正在拖哪条（用于高亮；null = 没在拖） */
  dragging: ColumnHandle | null
  /** 挂到 .pl-body 上（量内容宽） */
  bodyRef: React.RefObject<HTMLDivElement | null>
  startDrag: (handle: ColumnHandle) => (e: React.MouseEvent) => void
  /** 双击拖拽条 = 回到默认宽度（并写盘） */
  reset: () => void
}

export function useColumnLayout(): ColumnLayout {
  const [cols, setCols] = useState<ColumnWidths>(readColumns)
  const [dragging, setDragging] = useState<ColumnHandle | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  /** 最新的宽度（拖拽过程中 setCols 是异步的，松手时要写"最终"那份） */
  const colsRef = useRef(cols)
  useEffect(() => {
    colsRef.current = cols
  }, [cols])

  const reset = useCallback(() => {
    const d = { ...DEFAULT_COLUMNS }
    colsRef.current = d
    setCols(d)
    saveColumns(d)
  }, [])

  // 窗口变小：按同一下限重新 clamp（不写盘 —— 这是"装不下"，不是新偏好）
  useEffect(() => {
    const onResize = () => {
      const available = contentWidth(bodyRef.current)
      if (!available) return
      const room = Math.max(0, available - SPLITTER_TOTAL)
      setCols((c) => {
        const factors = Math.min(
          c.factors,
          Math.max(MIN_COLUMNS.factors, room - c.identity - MIN_COLUMNS.preview),
        )
        const identity = Math.min(
          c.identity,
          Math.max(MIN_COLUMNS.identity, room - factors - MIN_COLUMNS.preview),
        )
        return factors === c.factors && identity === c.identity ? c : { factors, identity }
      })
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const startDrag = useCallback(
    (handle: ColumnHandle) => (e: React.MouseEvent) => {
      // 只认左键；右键/中键不该开始拖列
      if (e.button !== 0) return
      e.preventDefault()
      const startX = e.clientX
      const start = { ...colsRef.current }
      const available = contentWidth(bodyRef.current)
      let moved = false
      setDragging(handle)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'

      const onMove = (ev: MouseEvent) => {
        moved = true
        const next = resizeColumns(start, handle, ev.clientX - startX, available)
        colsRef.current = next
        setCols(next)
      }
      const onUp = () => {
        document.removeEventListener('mousemove', onMove)
        document.removeEventListener('mouseup', onUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        setDragging(null)
        // 只有真的拖过才写盘：单击一下拖拽条不该改动已保存的偏好
        if (moved) saveColumns(colsRef.current)
      }
      document.addEventListener('mousemove', onMove)
      document.addEventListener('mouseup', onUp)
    },
    [],
  )

  return { cols, dragging, bodyRef, startDrag, reset }
}
