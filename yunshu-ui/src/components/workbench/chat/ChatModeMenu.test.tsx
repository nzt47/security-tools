/**
 * ChatModeMenu 测试 —— 「对话模式」下拉（在工具栏「输出格式」右侧）
 * ------------------------------------------------------------------
 * 三档是**取舍**：轻量（默认，1 次模型调用）/ 检索（+≤3k token/轮）/ 完整（非流式、可能多轮调用）。
 * 本测试钉死两件事：
 *   ① 选档真的写进 store，并**随请求体发出**（后端据此选链路）；
 *   ② 持久化生效（刷新后仍是所选档）——否则用户每次开页面都要重选。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { ChatModeMenu } from './ChatModeMenu'
import { CHAT_PREFS_STORAGE_KEY, useChatPrefsStore } from '../../../stores/useChatPrefsStore'

function resetStore() {
  localStorage.clear()
  useChatPrefsStore.setState({ mode: 'plain' })
}

describe('ChatModeMenu · 对话模式三档', () => {
  beforeEach(resetStore)
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('默认显示「轻量」—— 与引入该开关之前的行为一致', () => {
    render(<ChatModeMenu />)
    expect(screen.getByRole('button')).toHaveTextContent('模式：轻量')
  })

  it('展开后列出三档，且每档都写明代价', () => {
    render(<ChatModeMenu />)
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('轻量')).toBeInTheDocument()
    expect(screen.getByText('检索')).toBeInTheDocument()
    expect(screen.getByText('完整')).toBeInTheDocument()
    // 说明里必须直说代价（"非流式""多轮"），不能只写好处。
    // 用 getAllByText：代价在多处出现（选项说明 + 底部提示），全页唯一性是**错的要求**
    expect(screen.getAllByText(/非流式/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/多次模型调用/).length).toBeGreaterThan(0)
  })

  it('选「检索」→ store 变更 + 持久化 + 按钮回显', () => {
    render(<ChatModeMenu />)
    fireEvent.click(screen.getByRole('button'))
    fireEvent.click(screen.getByText('检索'))

    expect(useChatPrefsStore.getState().mode).toBe('retrieval')
    expect(screen.getByRole('button')).toHaveTextContent('模式：检索')
    // 持久化：LocalStorage 里带了 mode（刷新后不丢）
    const raw = localStorage.getItem(CHAT_PREFS_STORAGE_KEY) ?? ''
    expect(raw).toContain('"mode":"retrieval"')
  })

  it('选「完整」→ 按钮转为醒目态（提醒这是重档）', () => {
    render(<ChatModeMenu />)
    fireEvent.click(screen.getByRole('button'))
    fireEvent.click(screen.getByText('完整'))
    expect(useChatPrefsStore.getState().mode).toBe('full')
    expect(screen.getByRole('button').className).toContain('violet')
  })

  it('Escape 关闭菜单且不改变选择', () => {
    render(<ChatModeMenu />)
    fireEvent.click(screen.getByRole('button'))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByText(/日常问答用轻量/)).not.toBeInTheDocument()
    expect(useChatPrefsStore.getState().mode).toBe('plain')
  })
})
