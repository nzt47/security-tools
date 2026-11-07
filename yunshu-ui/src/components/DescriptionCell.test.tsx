/**
 * 「说明」列的唯一口径（components/DescriptionCell）：三张表共用，不许再各写一份。
 *
 * 锁死 2026-11-07 的收口：
 *   1. 最多 3 行 + 自动换行（line-clamp-3 + wrap-break-word），不是 truncate；
 *   2. 空说明给「（无说明）」占位，不留空白；
 *   3. 只有传了 onExpand 才出「查看全文」（技能中心），工作流/工具表不出；
 *   4. 宽度上限来自常量 DESCRIPTION_MAX_WIDTH（可被 maxWidth 覆盖）——这是三表同宽同形的依据。
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DescriptionCell, { DESCRIPTION_MAX_WIDTH } from './DescriptionCell'

describe('DescriptionCell（三表共用的说明列）', () => {
  afterEach(cleanup)

  it('长说明：3 行截断 + 自动换行 + 悬停看全文', () => {
    const text = '很长的说明'.repeat(40)
    render(<DescriptionCell text={text} />)
    const clamped = screen.getByText(text)
    expect(clamped.className).toContain('line-clamp-3')
    expect(clamped.className).toContain('wrap-break-word')
    expect(clamped.className).not.toContain('truncate')
    expect(clamped.getAttribute('title')).toBe(text)
  })

  it('宽度上限默认取常量（三表同宽）；可被 maxWidth 覆盖', () => {
    const { container, unmount } = render(<DescriptionCell text='说明' />)
    const box = container.firstElementChild as HTMLElement
    expect(box.style.maxWidth).toBe(`${DESCRIPTION_MAX_WIDTH}px`)
    unmount()
    const { container: c2 } = render(<DescriptionCell text='说明' maxWidth={600} />)
    expect((c2.firstElementChild as HTMLElement).style.maxWidth).toBe('600px')
  })

  it('空/空白说明给「（无说明）」占位（不留空白单元格）', () => {
    render(<DescriptionCell text={'   '} />)
    expect(screen.getByText('（无说明）')).toBeInTheDocument()
  })

  it('只有传了 onExpand 才出「查看全文」（技能中心有弹窗，工作流/工具表没有）', () => {
    const onExpand = vi.fn()
    const { unmount } = render(<DescriptionCell text='说明' />)
    expect(screen.queryByText('查看全文')).toBeNull()
    unmount()
    render(<DescriptionCell text='说明' onExpand={onExpand} />)
    screen.getByText('查看全文').click()
    expect(onExpand).toHaveBeenCalledTimes(1)
  })
})
