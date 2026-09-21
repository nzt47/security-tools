/**
 * useChatPrefsStore · 显隐开关与风格的**真持久化**测试
 * ------------------------------------------------------------------
 * 为什么单开一个文件：已有 `useChatPrefsStore.test.ts` 只断言**内存态**
 * （setState 后 getState），**从未验证写入 LocalStorage、也从未验证重新水合**
 * ⇒ "显隐开关状态未持久化"这一风险没有任何测试兜底（覆盖空洞）。
 * 本文件驱动的是真链路：
 *   toggleDisplay/setStyle → `localStorage['yunshu:chat:prefs:v1']` 真的被写入
 *   → 清空内存态（模拟刷新）→ `persist.rehydrate()` → 开关与风格**原样恢复**。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  CHAT_PREFS_STORAGE_KEY,
  DEFAULT_STYLE,
  useChatPrefsStore,
} from './useChatPrefsStore'

const initial = useChatPrefsStore.getState()

function storedState(): Record<string, unknown> {
  const raw = localStorage.getItem(CHAT_PREFS_STORAGE_KEY)
  return raw ? (JSON.parse(raw).state as Record<string, unknown>) : {}
}

/** 模拟"刷新页面"：清内存态 → **保留刷新前的 LocalStorage** → 重新水合
 *
 * 注意（本测试踩过的坑）：zustand persist 的 setState 会**立即回写存储**，
 * 所以"清内存态"这一步会顺手把存储也覆盖成默认值。必须先取出刷新前的原始
 * 存储串，清完内存态后再放回去，否则测的就不是"刷新"而是"重置"。
 */
async function simulateReload() {
  const raw = localStorage.getItem(CHAT_PREFS_STORAGE_KEY)
  useChatPrefsStore.setState({
    ...DEFAULT_STYLE,
    showThinking: true,
    showToolCalls: true,
  })
  if (raw !== null) localStorage.setItem(CHAT_PREFS_STORAGE_KEY, raw)
  await useChatPrefsStore.persist.rehydrate()
}

describe('useChatPrefsStore · 持久化链路', () => {
  beforeEach(() => {
    useChatPrefsStore.setState({ ...initial })
    localStorage.clear()
  })
  afterEach(() => {
    localStorage.clear()
  })

  it('关闭「思考过程」开关后写入 LocalStorage，刷新后仍是关闭', async () => {
    useChatPrefsStore.getState().setDisplay('thinking', false)
    expect(storedState().showThinking).toBe(false)

    await simulateReload()
    expect(useChatPrefsStore.getState().showThinking).toBe(false)
    // 无关开关不受影响
    expect(useChatPrefsStore.getState().showToolCalls).toBe(true)
  })

  it('关闭「工具调用」开关后写入 LocalStorage，刷新后仍是关闭', async () => {
    useChatPrefsStore.getState().toggleDisplay('toolcalls')
    expect(storedState().showToolCalls).toBe(false)

    await simulateReload()
    expect(useChatPrefsStore.getState().showToolCalls).toBe(false)
  })

  it('两个开关都关闭 → 刷新后仍都关闭（不被默认值覆盖）', async () => {
    useChatPrefsStore.getState().setDisplay('thinking', false)
    useChatPrefsStore.getState().setDisplay('toolcalls', false)
    await simulateReload()
    const s = useChatPrefsStore.getState()
    expect([s.showThinking, s.showToolCalls]).toEqual([false, false])
  })

  it('输出格式 / 主题 / 气泡 / 字号四项同样持久化并可水合', async () => {
    useChatPrefsStore.getState().setStyle({
      format: 'terminal', theme: 'forest', bubbleStyle: 'modern', fontSize: 'large',
    })
    expect(storedState()).toMatchObject({
      format: 'terminal', theme: 'forest', bubbleStyle: 'modern', fontSize: 'large',
    })

    await simulateReload()
    const s = useChatPrefsStore.getState()
    expect([s.format, s.theme, s.bubbleStyle, s.fontSize]).toEqual([
      'terminal', 'forest', 'modern', 'large',
    ])
  })

  it('持久化只落盘 6 个偏好字段（partialize 契约不含方法）', () => {
    useChatPrefsStore.getState().setDisplay('thinking', false)
    expect(Object.keys(storedState()).sort()).toEqual(
      ['bubbleStyle', 'fontSize', 'format', 'showThinking', 'showToolCalls', 'theme'],
    )
  })

  it('LocalStorage 是脏值时回退默认（不白屏）', async () => {
    localStorage.setItem(
      CHAT_PREFS_STORAGE_KEY,
      JSON.stringify({ state: { showThinking: false, theme: 'nope' }, version: 1 }),
    )
    await simulateReload()
    const s = useChatPrefsStore.getState()
    // 未定义的键保持默认；显式合法的键被采纳
    expect(s.showThinking).toBe(false)
    expect(s.showToolCalls).toBe(true)
    expect(s.fontSize).toBe(DEFAULT_STYLE.fontSize)
  })

  it('resetStyle 只回默认风格，不覆盖已关闭的显隐开关（且持久化）', async () => {
    useChatPrefsStore.getState().setDisplay('thinking', false)
    useChatPrefsStore.getState().setStyle({ theme: 'purple' })
    useChatPrefsStore.getState().resetStyle()
    await simulateReload()
    const s = useChatPrefsStore.getState()
    expect(s.theme).toBe(DEFAULT_STYLE.theme)
    expect(s.showThinking).toBe(false)
  })
})
