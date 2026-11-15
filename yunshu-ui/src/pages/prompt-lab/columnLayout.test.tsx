/**
 * 提示词实验室三列宽度（可拖拽 + 自动保存）—— 回归锁
 * ------------------------------------------------------------------
 * Owner 2026-11-15：「改为列宽可拖拽并自动保存，由我来定列宽吧。」
 * 锁四件：
 *   ① 存档读写的健壮性（没存过 / 脏 JSON / 小到不合理的值 ⇒ 回默认，不白屏）；
 *   ② 拖拽只改被拖那一列，且三列下限与"② 至少 360px"是硬约束；
 *   ③ 松手即写 localStorage（连点两下不动不该改动已保存的偏好）；
 *   ④ 双击拖拽条 = 回默认并写盘。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import {
  DEFAULT_COLUMNS,
  MIN_COLUMNS,
  PL_COLUMNS_STORAGE_KEY,
  SPLITTER_TOTAL,
  readColumns,
  resizeColumns,
  saveColumns,
  useColumnLayout,
} from './columnLayout'

const stored = (): unknown => JSON.parse(localStorage.getItem(PL_COLUMNS_STORAGE_KEY) ?? 'null')

beforeEach(() => {
  localStorage.removeItem(PL_COLUMNS_STORAGE_KEY)
  document.body.style.cursor = ''
})

afterEach(cleanup)

describe('存档读写（脏数据不许白屏）', () => {
  it('没存过 ⇒ 默认 300 / 440', () => {
    expect(readColumns()).toEqual(DEFAULT_COLUMNS)
  })

  it('脏 JSON / 缺字段 / 小到不合理的值 ⇒ 逐项回默认', () => {
    localStorage.setItem(PL_COLUMNS_STORAGE_KEY, '{oops')
    expect(readColumns()).toEqual(DEFAULT_COLUMNS)

    localStorage.setItem(PL_COLUMNS_STORAGE_KEY, JSON.stringify({ factors: 400 }))
    expect(readColumns()).toEqual({ factors: 400, identity: DEFAULT_COLUMNS.identity })

    localStorage.setItem(PL_COLUMNS_STORAGE_KEY, JSON.stringify({ factors: 12, identity: 20 }))
    expect(readColumns()).toEqual(DEFAULT_COLUMNS)
  })

  it('saveColumns 写回同一份 JSON', () => {
    saveColumns({ factors: 360, identity: 500 })
    expect(stored()).toEqual({ factors: 360, identity: 500 })
    expect(readColumns()).toEqual({ factors: 360, identity: 500 })
  })
})

describe('resizeColumns：只改被拖的列，三列下限是硬约束', () => {
  // 1440 屏实测内容宽 ≈ 1376，两条拖拽条 24
  const available = 1376

  it('拖因素列的右边缘：只改因素列，另两列不动', () => {
    const next = resizeColumns(DEFAULT_COLUMNS, 'factors', 80, available)
    expect(next).toEqual({ factors: 380, identity: 440 })
  })

  it('因素列拖到下限 / 上限都停住（上限 = 给最终注入文本留够 360）', () => {
    expect(resizeColumns(DEFAULT_COLUMNS, 'factors', -9999, available).factors).toBe(MIN_COLUMNS.factors)
    const max = available - SPLITTER_TOTAL - DEFAULT_COLUMNS.identity - MIN_COLUMNS.preview
    expect(resizeColumns(DEFAULT_COLUMNS, 'factors', 9999, available).factors).toBe(max)
  })

  it('拖分节列：只改分节列（最终注入文本吸收剩余）', () => {
    const next = resizeColumns(DEFAULT_COLUMNS, 'identity', -60, available)
    expect(next).toEqual({ factors: 300, identity: 380 })
    expect(resizeColumns(DEFAULT_COLUMNS, 'identity', -9999, available).identity).toBe(MIN_COLUMNS.identity)
  })
})

/** 最小宿主：一条拖拽条 + 两列宽度，验证 hook 的拖拽/落盘/复位 */
function Harness() {
  const c = useColumnLayout()
  return (
    <div ref={c.bodyRef} data-testid="body">
      <div data-testid="factors" style={{ width: c.cols.factors }} />
      <div
        role="separator"
        aria-label="拖因素列"
        onMouseDown={c.startDrag('factors')}
        onDoubleClick={c.reset}
      />
      <div data-testid="identity" style={{ width: c.cols.identity }} />
    </div>
  )
}

describe('useColumnLayout：拖动 → 松手落盘；双击复位', () => {
  /** jsdom 没有布局，clientWidth 恒 0 ⇒ 手动给宿主一个 1400 的内容宽 */
  const mount = () => {
    render(<Harness />)
    const body = screen.getByTestId('body')
    Object.defineProperty(body, 'clientWidth', { configurable: true, value: 1400 })
    return body
  }

  it('拖动 +100 ⇒ 因素列 400，松手写盘', () => {
    mount()
    fireEvent.mouseDown(screen.getByRole('separator', { name: '拖因素列' }), { clientX: 500 })
    fireEvent.mouseMove(document, { clientX: 600 })
    fireEvent.mouseUp(document)

    expect(screen.getByTestId('factors').style.width).toBe('400px')
    expect(screen.getByTestId('identity').style.width).toBe(`${DEFAULT_COLUMNS.identity}px`)
    expect(stored()).toEqual({ factors: 400, identity: DEFAULT_COLUMNS.identity })
    // 拖动期间挂上的光标约束要收拾干净
    expect(document.body.style.cursor).toBe('')
  })

  it('只点一下不拖 ⇒ 不写盘（不改动已保存的偏好）', () => {
    mount()
    fireEvent.mouseDown(screen.getByRole('separator', { name: '拖因素列' }), { clientX: 500 })
    fireEvent.mouseUp(document)
    expect(localStorage.getItem(PL_COLUMNS_STORAGE_KEY)).toBeNull()
  })

  it('双击拖拽条 ⇒ 回默认并写盘', () => {
    saveColumns({ factors: 520, identity: 600 })
    mount()
    expect(screen.getByTestId('factors').style.width).toBe('520px')

    fireEvent.doubleClick(screen.getByRole('separator', { name: '拖因素列' }))
    expect(screen.getByTestId('factors').style.width).toBe(`${DEFAULT_COLUMNS.factors}px`)
    expect(stored()).toEqual(DEFAULT_COLUMNS)
  })
})
