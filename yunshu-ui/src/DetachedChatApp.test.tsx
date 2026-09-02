/**
 * DetachedChatApp 独立窗口测试 —— 缺陷 ③
 * ------------------------------------------------
 * 验收点：#/detached/chat 在 mock（installMockElectron 注入 window.electronAPI）
 * 下可打开，且与主工作台会话一致。
 * 本测试注入 mock electronAPI（getInitialState 返回分离瞬间快照）后挂载
 * <DetachedChatApp panelId="chat" />：
 *   1. CHAT 面板走与主工作台一致的 renderPanel（ContentPanel → 会话页），不再渲染
 *      独立的 ChatPanel/SidebarPanel 分支（渲染差异已消除）；
 *   2. 快照中的主工作台 messages 被恢复并渲染到对话流。
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DetachedChatApp } from '@/DetachedChatApp'
import { useLayoutStore } from '@/stores/useLayoutStore'
import type { ChatMessage } from '@/stores/useLayoutStore'
import type { StateSyncPayload, WindowMeta } from '@/electron/ipc'
import type { DetachPanelRequest } from '@/electron/ipc'

/** 模拟主工作台分离瞬间的会话快照（mockElectron 经 localStorage 暂存、getInitialState 取回） */
const SNAPSHOT: StateSyncPayload = {
  type: 'snapshot',
  messages: [
    { id: 'm1', role: 'user', content: '主工作台的问题', createdAt: 1700000000001, status: 'done' },
    { id: 'm2', role: 'assistant', content: '主工作台的回答', createdAt: 1700000000002, status: 'done' },
  ] as ChatMessage[],
  thinking: [],
}

/** mock electronAPI（等价 installMockElectron 注入的形状） */
function installMockApi(): void {
  window.electronAPI = {
    async detachPanel(_req: DetachPanelRequest): Promise<number> {
      return 1
    },
    async getWindowMeta(): Promise<WindowMeta> {
      return { isElectron: true, kind: 'detached', detachedPanelId: 'chat' }
    },
    async getInitialState(): Promise<StateSyncPayload | null> {
      return SNAPSHOT
    },
    broadcastState(): void {},
    onStateSync(): () => void {
      return () => {}
    },
  }
}

beforeAll(() => {
  // jsdom 无 scrollIntoView，ChatPanel 触底滚动 effect 需要它（与 ChatPanel.test 同款处理）
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn()
  }
})

afterEach(() => {
  delete window.electronAPI
  useLayoutStore.setState({ messages: [], thinking: [], streaming: false })
  vi.unstubAllGlobals()
})

describe('DetachedChatApp · #/detached/chat（缺陷③）', () => {
  it('mock 下可打开：注入 electronAPI 后渲染独立窗口视图', () => {
    installMockApi()
    // WorkbenchChatPage 挂载时请求 /api/sessions，stub 为空会话列表
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ sessions: [], current_id: null }) })),
    )
    const { container } = render(<DetachedChatApp panelId="chat" />)
    // 迷你顶栏标识独立窗口 + 标题（对话）
    expect(container.textContent).toContain('独立窗口')
    expect(container.textContent).toContain('云枢 · 对话')
  })

  it('会话一致：启动快照（主工作台 messages）被恢复并渲染', async () => {
    installMockApi()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ sessions: [], current_id: null }) })),
    )
    render(<DetachedChatApp panelId="chat" />)

    // getInitialState 异步恢复快照 → 对话流渲染出主工作台消息
    expect(await screen.findByText('主工作台的问题')).toBeInTheDocument()
    expect(screen.getByText('主工作台的回答')).toBeInTheDocument()
  })
})
