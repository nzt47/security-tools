/**
 * 列宽拖动 —— 三张能力表（技能中心 / 工具集 / 工作流）共用的 implementation。
 *
 * 2026-11-08 Operator 要求：工具集与工作流的表要「同技能一样列宽可自由调」。
 * 实现只留一份（`lib/tableCols` + `components/ColResizer`），故这里的用例锁的是
 * **那份实现的契约**：默认值 / 拖动 / 上下限夹紧 / 松手落盘 / 下次恢复 / 双击复位。
 */
import type { MouseEvent as ReactMouseEvent } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'

import ColResizer, { COL_RESIZER_LABEL, COL_RESIZER_TITLE } from '@/components/ColResizer'
import { COL_MAX_WIDTH, COL_MIN_WIDTH, useResizableColumns } from './tableCols'

const DEFAULTS = { name: 240, desc: 340 }
const STORE = 'yunshu.test.table.cols.v1'

/** 手柄按下事件的形状（真实现只读 clientX；preventDefault/stopPropagation 由真实事件提供） */
const press = (clientX: number) =>
  ({ clientX, preventDefault() {}, stopPropagation() {} }) as unknown as ReactMouseEvent

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup() })

describe('useResizableColumns（列宽拖动）', () => {
  it('初始用默认值；localStorage 里没记录时不写盘（不制造无意义持久化）', () => {
    const { result } = renderHook(() => useResizableColumns(DEFAULTS, STORE))
    expect(result.current.cols).toEqual(DEFAULTS)
    expect(localStorage.getItem(STORE)).toBeNull()
  })

  it('拖动按位移改宽度，松手才落盘（拖动过程中不写 localStorage）', () => {
    const { result } = renderHook(() => useResizableColumns(DEFAULTS, STORE))
    act(() => { result.current.startDrag('desc')(press(100)) })
    act(() => { window.dispatchEvent(new MouseEvent('mousemove', { clientX: 160 })) })
    expect(result.current.cols.desc).toBe(400)
    expect(localStorage.getItem(STORE)).toBeNull()
    act(() => { window.dispatchEvent(new MouseEvent('mouseup')) })
    expect(JSON.parse(localStorage.getItem(STORE) ?? '{}')).toEqual({ name: 240, desc: 400 })
  })

  it('宽度夹在 [COL_MIN_WIDTH, COL_MAX_WIDTH]：拖到负值/超长都不会把表毁掉', () => {
    const { result } = renderHook(() => useResizableColumns(DEFAULTS, STORE))
    act(() => { result.current.startDrag('name')(press(1000)) })
    act(() => { window.dispatchEvent(new MouseEvent('mousemove', { clientX: -5000 })) })
    expect(result.current.cols.name).toBe(COL_MIN_WIDTH)
    act(() => { window.dispatchEvent(new MouseEvent('mousemove', { clientX: 9000 })) })
    expect(result.current.cols.name).toBe(COL_MAX_WIDTH)
    act(() => { window.dispatchEvent(new MouseEvent('mouseup')) })
  })

  it('下次进来恢复上次存下的宽度（每个 storeKey 各记各的）', () => {
    localStorage.setItem(STORE, JSON.stringify({ name: 300, desc: 500 }))
    localStorage.setItem('yunshu.other.table.cols.v1', JSON.stringify({ name: 111, desc: 222 }))
    const { result } = renderHook(() => useResizableColumns(DEFAULTS, STORE))
    expect(result.current.cols).toEqual({ name: 300, desc: 500 })
  })

  it('双击复位只恢复该列（不是整表重置）', () => {
    localStorage.setItem(STORE, JSON.stringify({ name: 400, desc: 500 }))
    const { result } = renderHook(() => useResizableColumns(DEFAULTS, STORE))
    act(() => { result.current.reset('desc') })
    expect(result.current.cols).toEqual({ name: 400, desc: 340 })
    expect(JSON.parse(localStorage.getItem(STORE) ?? '{}')).toEqual({ name: 400, desc: 340 })
  })

  it('localStorage 坏 JSON / 不可用时静默降级为默认宽度（不抛错、不白屏）', () => {
    localStorage.setItem(STORE, '{ 不是 JSON')
    const { result } = renderHook(() => useResizableColumns(DEFAULTS, STORE))
    expect(result.current.cols).toEqual(DEFAULTS)

    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('QuotaExceeded') })
    const second = renderHook(() => useResizableColumns(DEFAULTS, 'yunshu.quota.table.cols.v1'))
    act(() => { second.result.current.startDrag('name')(press(0)) })
    act(() => { window.dispatchEvent(new MouseEvent('mousemove', { clientX: 40 })) })
    act(() => { window.dispatchEvent(new MouseEvent('mouseup')) })
    expect(second.result.current.cols.name).toBe(280)
    spy.mockRestore()
  })
})

describe('ColResizer（表头手柄）', () => {
  it('是可命名的 separator，带提示；按下与双击分别触发拖拽与复位', () => {
    const onStart = vi.fn()
    const onReset = vi.fn()
    render(<ColResizer onStart={onStart} onReset={onReset} />)
    const handle = screen.getByRole('separator', { name: COL_RESIZER_LABEL })
    expect(handle.getAttribute('title')).toBe(COL_RESIZER_TITLE)
    expect(handle.className).toContain('cursor-col-resize')
    fireEvent.mouseDown(handle)
    fireEvent.doubleClick(handle)
    expect(onStart).toHaveBeenCalledTimes(1)
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})
