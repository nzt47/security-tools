/**
 * 概览卡「LLM 缓存命中」（2026-11-08）
 *
 * 数据来源：/api/health/probe-trend → probe_details.l3_llm_tool.metrics
 * （L3 探针从 LLMMonitor.get_stats() 取 cache_hit_ratio，口径 = 只认 API 上报过缓存字段的调用）。
 * 这里的两个用例守：有字段就显示百分比、没字段就 MISSING（不许编 0% 或 100%）。
 */
import { describe, expect, it } from 'vitest'

import { MISSING, overviewCards, type ProbePoint } from './health'

function pointWith(metrics?: Record<string, number>): ProbePoint {
  return {
    timestamp: '2026-11-08T18:00:00+08:00',
    overall: 0.9,
    dimensions: {},
    probe_details: {
      l3_llm_tool: {
        available: true,
        score: 0.9,
        detail: 'chat_error_rate=0.0%',
        ...(metrics ? { metrics } : {}),
      },
    },
  }
}

const cents = (ratio: number, hit: number, miss: number) => ({
  cache_hit_ratio: ratio,
  cache_hit_tokens: hit,
  cache_miss_tokens: miss,
  cache_reported_calls: 21,
  llm_calls: 21,
})

describe('概览卡 · LLM 缓存命中', () => {
  it('有 L3 metrics 时显示百分比与命中/未命中 token', () => {
    const cards = overviewCards(undefined, undefined, undefined, pointWith(cents(0.881, 259584, 35132)))
    const card = cards.find((c) => c.key === 'cache-hit')!
    expect(card).toBeTruthy()
    expect(card.value).toBe('88.1%')
    expect(card.missing).toBe(false)
    expect(card.tone).toBe('success')
  })

  it('命中率偏低时用 warning 色（不静默）', () => {
    const cards = overviewCards(undefined, undefined, undefined, pointWith(cents(0.31, 1000, 2226)))
    expect(cards.find((c) => c.key === 'cache-hit')!.tone).toBe('warning')
  })

  it('没有缓存字段时显示 MISSING（不编 0%）', () => {
    const cards = overviewCards(undefined, undefined, undefined, pointWith(undefined))
    const card = cards.find((c) => c.key === 'cache-hit')!
    expect(card.value).toBe(MISSING)
    expect(card.missing).toBe(true)
  })
})
