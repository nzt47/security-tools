/**
 * transcriptFold 判定矩阵（纯函数，2026-11-28）
 * ------------------------------------------------------------------
 * 钉住四档 × 折叠时机 × 轮次位置 的组合，以及「仅最终回复」的中间回复判定。
 */
import { describe, expect, it } from 'vitest'
import {
  computeTurnMeta,
  foldPreview,
  shouldFoldProcess,
  shouldHideIntermediateReply,
} from './transcriptFold'

const base = {
  timing: 'completion' as const,
  status: 'done' as const,
  isHistory: false,
  turnClosed: false,
  hasSteps: true,
}

describe('computeTurnMeta', () => {
  it('按 user 消息切轮，并算出是否翻篇 / 是否轮内最后一条回复', () => {
    const meta = computeTurnMeta([
      { id: 'u1', role: 'user' },
      { id: 'a1', role: 'assistant' },
      { id: 'a2', role: 'assistant' },
      { id: 'u2', role: 'user' },
      { id: 'a3', role: 'assistant' },
    ])
    expect(meta.map((m) => m.turnId)).toEqual(['u1', 'u1', 'u1', 'u2', 'u2'])
    expect(meta.map((m) => m.turnClosed)).toEqual([true, true, true, false, false])
    // 轮 1 的最后一条回复是 a2（a1 是中间回复）；user 行该字段无意义，恒 true
    expect(meta.map((m) => m.isLastAssistantInTurn)).toEqual([true, false, true, true, true])
  })
})

describe('shouldFoldProcess', () => {
  it('紧凑/标准：完成的轮次一律折叠（不看是否历史）', () => {
    expect(shouldFoldProcess({ ...base, mode: 'compact' })).toBe(true)
    expect(shouldFoldProcess({ ...base, mode: 'standard', isHistory: true })).toBe(true)
  })

  it('详细：只折叠历史轮；当前轮要等翻篇', () => {
    expect(shouldFoldProcess({ ...base, mode: 'detailed' })).toBe(false)
    expect(shouldFoldProcess({ ...base, mode: 'detailed', turnClosed: true })).toBe(true)
    expect(shouldFoldProcess({ ...base, mode: 'detailed', isHistory: true })).toBe(true)
  })

  it('冗长：永不折叠', () => {
    expect(shouldFoldProcess({ ...base, mode: 'verbose', turnClosed: true, isHistory: true })).toBe(false)
  })

  it('进行中 / 无步骤 / 下次输入才折叠 的边界', () => {
    expect(shouldFoldProcess({ ...base, mode: 'compact', status: 'streaming' })).toBe(false)
    expect(shouldFoldProcess({ ...base, mode: 'compact', hasSteps: false })).toBe(false)
    expect(shouldFoldProcess({ ...base, mode: 'compact', timing: 'next-input' })).toBe(false)
    expect(shouldFoldProcess({ ...base, mode: 'compact', timing: 'next-input', turnClosed: true })).toBe(true)
  })
})

describe('foldPreview', () => {
  const steps = [
    { title: '阶段：分析', detail: '先看上下文' },
    { title: '工具调用：ppt_read', detail: 'path: a.pptx' },
  ]

  it('紧凑档不显示预览', () => {
    expect(foldPreview('compact', steps)).toBe('')
  })

  it('标准/详细取最后一步的标题（去掉工具前缀）', () => {
    expect(foldPreview('standard', steps)).toBe('ppt_read')
    expect(foldPreview('detailed', steps)).toBe('ppt_read')
  })

  it('超长截断到 40 字', () => {
    const long = [{ title: '工具调用：' + 'x'.repeat(60), detail: '' }]
    const preview = foldPreview('standard', long)
    expect(preview.endsWith('…')).toBe(true)
    expect(preview.length).toBeLessThanOrEqual(41)
  })
})

describe('shouldHideIntermediateReply', () => {
  const meta = { turnId: 'u1', turnClosed: true, isLastAssistantInTurn: false }

  it('仅最终回复 + 已折叠 + 非轮内最后一条 ⇒ 隐藏', () => {
    expect(shouldHideIntermediateReply('final', true, meta)).toBe(true)
  })

  it('其余组合都不隐藏', () => {
    expect(shouldHideIntermediateReply('all', true, meta)).toBe(false)
    expect(shouldHideIntermediateReply('final', false, meta)).toBe(false)
    expect(shouldHideIntermediateReply('final', true, { ...meta, isLastAssistantInTurn: true })).toBe(false)
    expect(shouldHideIntermediateReply('final', true, undefined)).toBe(false)
  })
})
