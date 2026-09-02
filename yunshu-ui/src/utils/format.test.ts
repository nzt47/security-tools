/**
 * format 工具单元测试（零依赖纯函数，TZ 无关：使用本地时间构造 Date）
 */
import { describe, it, expect } from 'vitest'
import { formatDate, formatBytes, formatNumber } from './format'

describe('formatDate', () => {
  it('默认 pattern：YYYY-MM-DD HH:mm:ss（本地时间）', () => {
    const date = new Date(2026, 0, 5, 9, 8, 7) // 2026-01-05 09:08:07（月从 0 起）
    expect(formatDate(date)).toBe('2026-01-05 09:08:07')
  })

  it('支持时间戳与 ISO 字符串输入', () => {
    const ts = new Date(2026, 1, 3, 12, 30, 0).getTime()
    expect(formatDate(ts, 'YYYY/MM/DD')).toBe('2026/02/03')
    expect(formatDate(new Date(2026, 1, 3, 12, 30, 0).toISOString(), 'HH:mm')).toMatch(/^\d{2}:\d{2}$/)
  })

  it('非法输入返回空字符串', () => {
    expect(formatDate('not-a-date')).toBe('')
    expect(formatDate(Number.NaN)).toBe('')
  })
})

describe('formatBytes', () => {
  it('边界与单位换算', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1024)).toBe('1 KB')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(1048576)).toBe('1 MB')
    expect(formatBytes(1099511627776)).toBe('1 TB')
  })

  it('非法输入回退 0 B', () => {
    expect(formatBytes(-1)).toBe('0 B')
    expect(formatBytes(Number.NaN)).toBe('0 B')
  })
})

describe('formatNumber', () => {
  it('千分位分组', () => {
    expect(formatNumber(1234567.891)).toBe('1,234,567.891')
    expect(formatNumber(1000)).toBe('1,000')
    expect(formatNumber(-1234.5)).toBe('-1,234.5')
  })

  it('非法输入回退 0', () => {
    expect(formatNumber(Number.NaN)).toBe('0')
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe('0')
  })
})
