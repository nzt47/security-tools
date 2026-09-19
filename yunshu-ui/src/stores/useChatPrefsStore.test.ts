/**
 * useChatPrefsStore —— 对话显示开关与风格偏好测试
 * ------------------------------------------------------------------
 * 覆盖需求：
 *  - 「恢复工具调用过程和思考过程的显示功能 + 显示/隐藏开关」（display 开关与持久化）；
 *  - 「多种对话输出格式切换 + 多种对话风格选择」（format/theme/bubble/fontSize + CSS 变量折算）。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  CHAT_BUBBLE_STYLES,
  CHAT_FONT_SIZES,
  CHAT_FORMATS,
  CHAT_THEMES,
  CHAT_PREFS_STORAGE_KEY,
  DEFAULT_STYLE,
  chatStyleVars,
  useChatPrefsStore,
} from './useChatPrefsStore'

const initial = useChatPrefsStore.getState()

beforeEach(() => {
  useChatPrefsStore.setState({ ...initial })
  try {
    localStorage.removeItem(CHAT_PREFS_STORAGE_KEY)
  } catch {
    /* jsdom 外无 localStorage */
  }
})

describe('显示开关（思考 / 工具调用）', () => {
  it('默认两项都显示（与 legacy 默认一致）', () => {
    const s = useChatPrefsStore.getState()
    expect(s.showThinking).toBe(true)
    expect(s.showToolCalls).toBe(true)
  })

  it('toggleDisplay 分别切换思考与工具调用，互不影响', () => {
    useChatPrefsStore.getState().toggleDisplay('thinking')
    expect(useChatPrefsStore.getState().showThinking).toBe(false)
    expect(useChatPrefsStore.getState().showToolCalls).toBe(true)

    useChatPrefsStore.getState().toggleDisplay('toolcalls')
    expect(useChatPrefsStore.getState().showToolCalls).toBe(false)

    useChatPrefsStore.getState().toggleDisplay('thinking')
    expect(useChatPrefsStore.getState().showThinking).toBe(true)
  })

  it('setDisplay 幂等设置指定开关', () => {
    useChatPrefsStore.getState().setDisplay('thinking', false)
    useChatPrefsStore.getState().setDisplay('thinking', false)
    expect(useChatPrefsStore.getState().showThinking).toBe(false)
    useChatPrefsStore.getState().setDisplay('toolcalls', false)
    expect(useChatPrefsStore.getState().showToolCalls).toBe(false)
  })
})

describe('对话输出格式与风格', () => {
  it('输出格式提供气泡/紧凑/终端三种', () => {
    expect(Object.keys(CHAT_FORMATS)).toEqual(['bubble', 'compact', 'terminal'])
  })

  it('颜色主题 5 套、气泡样式 3 档、字号 3 档（与 legacy 一致）', () => {
    expect(Object.keys(CHAT_THEMES)).toEqual(['midnight', 'ocean', 'forest', 'warm', 'purple'])
    expect(Object.keys(CHAT_BUBBLE_STYLES)).toEqual(['rounded', 'modern', 'minimal'])
    expect(Object.keys(CHAT_FONT_SIZES)).toEqual(['small', 'normal', 'large'])
  })

  it('setStyle 局部更新；resetStyle 恢复默认四项', () => {
    useChatPrefsStore.getState().setStyle({ format: 'terminal', theme: 'ocean' })
    let s = useChatPrefsStore.getState()
    expect(s.format).toBe('terminal')
    expect(s.theme).toBe('ocean')
    expect(s.bubbleStyle).toBe(DEFAULT_STYLE.bubbleStyle)

    useChatPrefsStore.getState().setStyle({ bubbleStyle: 'minimal', fontSize: 'large' })
    s = useChatPrefsStore.getState()
    expect(s.bubbleStyle).toBe('minimal')
    expect(s.fontSize).toBe('large')

    useChatPrefsStore.getState().resetStyle()
    s = useChatPrefsStore.getState()
    expect([s.format, s.theme, s.bubbleStyle, s.fontSize]).toEqual([
      DEFAULT_STYLE.format,
      DEFAULT_STYLE.theme,
      DEFAULT_STYLE.bubbleStyle,
      DEFAULT_STYLE.fontSize,
    ])
  })

  it('resetStyle 不动显示开关（两组偏好相互独立）', () => {
    useChatPrefsStore.getState().setDisplay('thinking', false)
    useChatPrefsStore.getState().setStyle({ theme: 'purple' })
    useChatPrefsStore.getState().resetStyle()
    expect(useChatPrefsStore.getState().showThinking).toBe(false)
    expect(useChatPrefsStore.getState().theme).toBe(DEFAULT_STYLE.theme)
  })
})

describe('chatStyleVars', () => {
  it('折算主题 / 气泡 / 字号为 CSS 变量', () => {
    const vars = chatStyleVars({ theme: 'forest', bubbleStyle: 'minimal', fontSize: 'large' })
    expect(vars['--chat-bot-bg']).toBe(CHAT_THEMES.forest.vars['--chat-bot-bg'])
    expect(vars['--chat-user-border']).toBe(CHAT_THEMES.forest.vars['--chat-user-border'])
    expect(vars['--chat-bubble-radius']).toBe(CHAT_BUBBLE_STYLES.minimal.vars['--chat-bubble-radius'])
    expect(vars['--chat-bubble-radius-user']).toBe(CHAT_BUBBLE_STYLES.minimal.vars['--chat-bubble-radius-user'])
    expect(vars['--chat-fs']).toBe(CHAT_FONT_SIZES.large.value)
  })

  it('未知名回退到默认主题/气泡/字号（脏 LocalStorage 不白屏）', () => {
    const vars = chatStyleVars({
      theme: 'nope' as never,
      bubbleStyle: 'nope' as never,
      fontSize: 'nope' as never,
    })
    expect(vars['--chat-bot-bg']).toBe(CHAT_THEMES.midnight.vars['--chat-bot-bg'])
    expect(vars['--chat-bubble-radius']).toBe(CHAT_BUBBLE_STYLES.rounded.vars['--chat-bubble-radius'])
    expect(vars['--chat-fs']).toBe('14px')
  })
})
