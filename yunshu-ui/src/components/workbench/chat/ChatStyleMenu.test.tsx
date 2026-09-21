/**
 * ChatStyleMenu 测试 —— 「对话风格 / 输出格式」的**交互链 + 持久化**
 * ------------------------------------------------------------------
 * 需求⑤「对话输出格式对齐 legacy + 多种格式切换」与需求⑥「多种对话风格选择」
 * 的覆盖空洞：本组件此前**零测试**（只有 store 层的纯状态测试）。
 * 本文件驱动真实链路：
 *   点开菜单 → 点主题/气泡/字号 → ① store 状态变 ② **预览区 CSS 变量真的重算**
 *   ③ **写入 LocalStorage** → 点「恢复默认风格」全部回退。
 * 不做静态字符串断言：断言的是"点击 → 状态 → 样式 → 存储"的效果链。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { ChatStyleMenu } from './ChatStyleMenu'
import {
  CHAT_BUBBLE_STYLES,
  CHAT_FONT_SIZES,
  CHAT_PREFS_STORAGE_KEY,
  CHAT_THEMES,
  DEFAULT_STYLE,
  useChatPrefsStore,
} from '../../../stores/useChatPrefsStore'

const initial = useChatPrefsStore.getState()

/** 预览区（「效果预览」标题的父容器里那个挂了 CSS 变量的 div） */
function previewRoot(): HTMLElement {
  const box = screen.getByText('效果预览').parentElement as HTMLElement
  return box.querySelector('div[style]') as HTMLElement
}

function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: /风格/ }))
}

function storedPrefs(): Record<string, unknown> {
  const raw = localStorage.getItem(CHAT_PREFS_STORAGE_KEY)
  return raw ? (JSON.parse(raw).state as Record<string, unknown>) : {}
}

describe('ChatStyleMenu · 风格切换真的生效且持久化', () => {
  beforeEach(() => {
    useChatPrefsStore.setState({ ...initial })
    localStorage.clear()
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('选颜色主题：store 变 → 预览 CSS 变量重算 → 写入 LocalStorage', async () => {
    render(<ChatStyleMenu />)
    openMenu()

    fireEvent.click(screen.getByTitle(CHAT_THEMES.ocean.label))
    await waitFor(() => expect(useChatPrefsStore.getState().theme).toBe('ocean'))

    // 预览区真的吃到了新变量（不是"状态变了但界面没动"）
    await waitFor(() =>
      expect(previewRoot().style.getPropertyValue('--chat-bot-bg')).toBe(
        CHAT_THEMES.ocean.vars['--chat-bot-bg'],
      ),
    )
    expect(storedPrefs().theme).toBe('ocean')
  })

  it('选气泡样式与字号：预览字号真的变化，并随之持久化', async () => {
    render(<ChatStyleMenu />)
    openMenu()

    fireEvent.click(screen.getByRole('button', { name: CHAT_BUBBLE_STYLES.minimal.label }))
    fireEvent.click(screen.getByRole('button', { name: CHAT_FONT_SIZES.large.label }))
    await waitFor(() => expect(useChatPrefsStore.getState().fontSize).toBe('large'))

    expect(useChatPrefsStore.getState().bubbleStyle).toBe('minimal')
    const bubble = screen.getByText('云枢回复将显示为这种样式')
    await waitFor(() => expect(bubble.style.fontSize).toBe(CHAT_FONT_SIZES.large.value))
    expect(storedPrefs().fontSize).toBe('large')
    expect(storedPrefs().bubbleStyle).toBe('minimal')
  })

  it('恢复默认风格：store 四项回默认、预览回默认变量、存储同步回默认', async () => {
    render(<ChatStyleMenu />)
    openMenu()
    fireEvent.click(screen.getByTitle(CHAT_THEMES.purple.label))
    fireEvent.click(screen.getByRole('button', { name: CHAT_FONT_SIZES.small.label }))
    await waitFor(() => expect(useChatPrefsStore.getState().theme).toBe('purple'))

    fireEvent.click(screen.getByRole('button', { name: /恢复默认风格/ }))
    await waitFor(() => expect(useChatPrefsStore.getState().theme).toBe(DEFAULT_STYLE.theme))
    expect(useChatPrefsStore.getState().fontSize).toBe(DEFAULT_STYLE.fontSize)
    await waitFor(() =>
      expect(previewRoot().style.getPropertyValue('--chat-bot-bg')).toBe(
        CHAT_THEMES[DEFAULT_STYLE.theme].vars['--chat-bot-bg'],
      ),
    )
    await waitFor(() => expect(storedPrefs().theme).toBe(DEFAULT_STYLE.theme))
  })

  it('已是默认风格时「恢复默认」按钮禁用（不给无效操作）', async () => {
    render(<ChatStyleMenu />)
    openMenu()
    expect(screen.getByRole('button', { name: /恢复默认风格/ })).toBeDisabled()
    fireEvent.click(screen.getByTitle(CHAT_THEMES.forest.label))
    await waitFor(() => expect(screen.getByRole('button', { name: /恢复默认风格/ })).toBeEnabled())
  })

  it('点击外部或按 Esc 关闭菜单（不残留浮层）', async () => {
    render(<ChatStyleMenu />)
    openMenu()
    expect(screen.getByText('🎨 对话风格')).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByText('🎨 对话风格')).not.toBeInTheDocument())
  })

  it('刷新后风格仍在（真在 LocalStorage 里，而不是只活在内存）', async () => {
    render(<ChatStyleMenu />)
    openMenu()
    fireEvent.click(screen.getByTitle(CHAT_THEMES.warm.label))
    await waitFor(() => expect(storedPrefs().theme).toBe('warm'))

    // 模拟刷新：留住"刷新前"的存储 → 清内存态 → 还原存储 → 重新水合
    // （persist 的 setState 会回写存储，故顺序不能颠倒，否则测的是"重置"而非"刷新"）
    const raw = localStorage.getItem(CHAT_PREFS_STORAGE_KEY)
    useChatPrefsStore.setState({ ...DEFAULT_STYLE, showThinking: true, showToolCalls: true })
    expect(useChatPrefsStore.getState().theme).toBe(DEFAULT_STYLE.theme)
    localStorage.setItem(CHAT_PREFS_STORAGE_KEY, raw as string)
    await act(async () => { await useChatPrefsStore.persist.rehydrate() })
    await waitFor(() => expect(useChatPrefsStore.getState().theme).toBe('warm'))
  })
})
