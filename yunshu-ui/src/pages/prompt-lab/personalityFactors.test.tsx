/**
 * 人格 6 维 ↔ /api/personality 的同步（§11.124 D-2 轻量版）—— 回归锁
 * ------------------------------------------------------------------
 * 锁三件，都是"面板在动、后端没变"的来源：
 *   ① 首屏 GET 用 custom_params **覆盖**默认值（否则界面显示的是 lib 默认档，拖一次就写回默认值）；
 *   ② 变化后**防抖 400ms** 只发一次 POST（拖动每帧打接口既慢又会把后端写爆）；
 *   ③ 只发**变动的那几维**（整份 6 键提交会在"人格 Tab 改 A、回实验室拖 B"时把 A 覆盖回旧值）。
 * 另锁"读不到后端"这一态：值保持默认、错误如实上抛（界面据此说明现在不是后端值）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor, cleanup } from '@testing-library/react'
import { usePromptLabStore } from '../../stores/usePromptLabStore'
import {
  CATEGORIES,
  DEFAULT_FACTORS,
  DEFAULT_VALUES,
  PERSONALITY_DIMENSIONS,
  PERSONALITY_FACTOR_IDS,
  personalityParamsDiff,
  personalityParamsOf,
} from '../../lib/promptFactors'
import { PERSONALITY_PARAMS } from '@/api/endpoints'
import { PERSONALITY_DEBOUNCE_MS, usePersonalityFactors } from './personalityFactors'

const api = vi.hoisted(() => ({ getEnvelope: vi.fn(), postEnvelope: vi.fn() }))

vi.mock('@/api/envelope', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getEnvelope: api.getEnvelope,
  postEnvelope: api.postEnvelope,
}))

/** 最小宿主：把 store 的 6 维与同步状态摊在同一屏，便于断言 */
function Harness() {
  const values = usePromptLabStore((s) => s.values)
  const setValues = usePromptLabStore((s) => s.setValues)
  const sync = usePersonalityFactors(values, setValues)
  return (
    <div>
      <span data-testid="tone">{String(values['personality.tone'])}</span>
      <span data-testid="humor">{String(values['personality.humor'])}</span>
      <span data-testid="state">
        {sync.loading ? 'loading' : sync.error ? 'error' : sync.saving ? 'saving' : sync.syncedAt ? 'synced' : 'idle'}
      </span>
      <span data-testid="error">{sync.error}</span>
      <button type="button" onClick={() => setValues({ 'personality.tone': 0.12 })}>
        set-tone
      </button>
      <button type="button" onClick={() => setValues({ 'personality.humor': 0.34 })}>
        set-humor
      </button>
    </div>
  )
}

beforeEach(() => {
  usePromptLabStore.setState({ values: { ...DEFAULT_VALUES } })
  api.getEnvelope.mockReset()
  api.postEnvelope.mockReset()
  api.postEnvelope.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('人格 6 维的因素定义', () => {
  it('分类表里有「人格」，且 6 个滑块是 0–1 / step 0.01 并带左右端文案', () => {
    expect(CATEGORIES.some((c) => c.id === 'personality')).toBe(true)

    const defs = DEFAULT_FACTORS.filter((f) => f.category === 'personality')
    expect(defs).toHaveLength(6)
    expect(defs.map((d) => d.id)).toEqual(PERSONALITY_FACTOR_IDS)
    for (const d of defs) {
      expect(d.control).toBe('slider')
      expect([d.min, d.max, d.step]).toEqual([0, 1, 0.01])
      // 左右端文案照后端 dimensions（0–1 的滑块没有单位，光看数字不知道两端是什么）
      expect(d.leftLabel).toBeTruthy()
      expect(d.rightLabel).toBeTruthy()
    }
    // 默认值取后端 _default().custom_params
    expect(personalityParamsOf(DEFAULT_VALUES)).toEqual({
      tone: 0.6,
      emotion: 0.7,
      conciseness: 0.4,
      initiative: 0.5,
      humor: 0.3,
      empathy: 0.8,
    })
    expect(PERSONALITY_DIMENSIONS).toHaveLength(6)
  })

  it('personalityParamsDiff 只挑出真正变了的键（后端是合并语义，整份提交会覆盖别处改动）', () => {
    const base = personalityParamsOf(DEFAULT_VALUES)
    expect(personalityParamsDiff(base, base)).toEqual({})
    expect(personalityParamsDiff(base, { ...base, tone: 0.1 })).toEqual({ tone: 0.1 })
  })
})

describe('usePersonalityFactors：首屏读数 / 防抖写回 / 读失败', () => {
  it('首屏 GET 用 custom_params 覆盖默认值，未返回的维度保留默认，且不会立刻回写', async () => {
    api.getEnvelope.mockResolvedValue({
      current_profile: 'professional',
      custom_params: { tone: 0.2, humor: 0.9 },
    })
    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('tone').textContent).toBe('0.2'))
    expect(screen.getByTestId('humor').textContent).toBe('0.9')
    // 后端没返回的维度：保留 lib 默认（0.8），不要被"清空"
    expect(usePromptLabStore.getState().values['personality.empathy']).toBe(0.8)
    expect(api.getEnvelope).toHaveBeenCalledTimes(1)

    // 刚读到就回写 = 白打一次接口；推进一个完整的防抖窗口也不该有 POST
    vi.useFakeTimers()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PERSONALITY_DEBOUNCE_MS + 200)
    })
    expect(api.postEnvelope).not.toHaveBeenCalled()
  })

  it('连拖两维 → 只在停手 400ms 后发一次 POST，且只带变动键', async () => {
    api.getEnvelope.mockResolvedValue({ current_profile: 'custom', custom_params: {} })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('idle'))

    vi.useFakeTimers()
    fireEvent.click(screen.getByText('set-tone'))
    // 还没到防抖窗口（少 50ms）⇒ 一次都不该发
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PERSONALITY_DEBOUNCE_MS - 50)
    })
    expect(api.postEnvelope).not.toHaveBeenCalled()

    // 窗口内又改了一维 ⇒ 计时重置，最终只发一次、带上两维
    fireEvent.click(screen.getByText('set-humor'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PERSONALITY_DEBOUNCE_MS + 50)
    })
    expect(api.postEnvelope).toHaveBeenCalledTimes(1)
    expect(api.postEnvelope).toHaveBeenCalledWith(PERSONALITY_PARAMS, {
      params: { tone: 0.12, humor: 0.34 },
    })
    expect(screen.getByTestId('state').textContent).toBe('synced')
  })

  it('读不到后端：值保持默认，错误如实上抛（界面据此说明现在不是后端值）', async () => {
    api.getEnvelope.mockRejectedValue(new Error('HTTP 401'))
    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('error'))
    expect(screen.getByTestId('error').textContent).toContain('HTTP 401')
    expect(screen.getByTestId('tone').textContent).toBe('0.6')
    expect(api.postEnvelope).not.toHaveBeenCalled()
  })
})
