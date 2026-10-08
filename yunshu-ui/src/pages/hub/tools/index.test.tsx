/**
 * 工具调用 Tab 容器测试
 * ------------------------------------------------------------------
 * 需求：「将工具调用的子项也做成 Tab」；2026-10-08 第 5 个子项「主线管理」**迁出**
 * （并入「装配车间 → 分身创建与组装」，见 pages/hub/workshop/agents.tsx）。
 * 锁死的不变量：
 *   - 4 个子项（工具集 / CLI 软件 / MCP 系统 / Computer Use）作为 Tab 并列，
 *     顺序与原子模块顺序一致；**「主线管理」不得在本容器里再出现**（禁止双入口）；
 *   - 点击 Tab 切换选中态与内容组件（role=tab + aria-selected + 内容重挂载）；
 *   - Tab 选择记忆在 localStorage（重开页面回到上次子模块），非法值回落「工具集」，
 *     **且迁走前的旧值 `'lines'` 也必须回落**（老记忆值不白屏、不复活已删 Tab）。
 *
 * 子模块自身会发 HTTP 请求 → 统一 stub fetch（返回空数据），只验证容器行为。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'

// 子模块各自 fetch 外部接口：返回「空但合法」的响应，避免真实网络
const fetchMock = vi.fn(async (url: string) => ({
  ok: true,
  status: 200,
  json: async () => (String(url).includes('/api/') ? {} : []),
  text: async () => '',
}))
vi.stubGlobal('fetch', fetchMock)

const { default: ToolsPage, TOOLS_TABS, TOOLS_TAB_STORAGE_KEY } = await import('./index')

describe('工具调用 · Tab 结构', () => {
  beforeEach(() => {
    localStorage.clear()
    fetchMock.mockClear()
  })
  afterEach(cleanup)

  it('4 个子项作为 Tab 并列（名称与顺序与原子模块一致；主线管理已迁出）', () => {
    render(<ToolsPage />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual([
      '工具集',
      'CLI 软件',
      'MCP 系统',
      'Computer Use',
    ])
    expect(TOOLS_TABS.map((t) => t.id)).toEqual([
      'toolset',
      'cli',
      'mcp',
      'computer-use',
    ])
    expect(TOOLS_TABS.map((t) => t.id)).not.toContain('lines')
    expect(screen.queryByRole('tab', { name: /主线管理/ })).toBeNull()
  })

  it('默认选中「工具集」（无记忆时），并渲染其内容', async () => {
    render(<ToolsPage />)
    const toolsetTab = screen.getByRole('tab', { name: /工具集/ })
    expect(toolsetTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('工具调用')
    // 子模块懒加载：等待其挂载后应出现该模块自己的 PageHeader 标题
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  })

  it('点击 Tab 切换选中态并记忆到 localStorage', async () => {
    render(<ToolsPage />)
    fireEvent.click(screen.getByRole('tab', { name: /MCP 系统/ }))
    expect(screen.getByRole('tab', { name: /MCP 系统/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /工具集/ })).toHaveAttribute('aria-selected', 'false')
    expect(localStorage.getItem(TOOLS_TAB_STORAGE_KEY)).toBe('mcp')
  })

  it('Tab 迁走后，记忆里的旧值 lines 回落「工具集」（不白屏、不复活已删 Tab）', () => {
    localStorage.setItem(TOOLS_TAB_STORAGE_KEY, 'lines')
    render(<ToolsPage />)
    expect(screen.getByRole('tab', { name: /工具集/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: /主线管理/ })).toBeNull()
    // 【为什么按 name 取】子模块懒加载完成后自己也有一个 h1（各子模块自带 PageHeader），
    // 故这里必须点名容器标题，而不是"页面里唯一那个 h1"。
    expect(screen.getByRole('heading', { level: 1, name: /工具调用/ })).toBeInTheDocument()
  })

  it('initialTab 优先于记忆值，非法值回落「工具集」', () => {
    localStorage.setItem(TOOLS_TAB_STORAGE_KEY, 'mcp')
    const { unmount } = render(<ToolsPage initialTab="cli" />)
    // initialTab 覆盖记忆值（并把它写回记忆）
    expect(screen.getByRole('tab', { name: /CLI 软件/ })).toHaveAttribute('aria-selected', 'true')
    unmount()

    // 无记忆 + 非法 initialTab → 回落第一个 Tab「工具集」
    localStorage.clear()
    render(<ToolsPage initialTab={'nope' as never} />)
    expect(screen.getByRole('tab', { name: /工具集/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('非法 initialTab 且已有合法记忆时，沿用记忆值', () => {
    localStorage.setItem(TOOLS_TAB_STORAGE_KEY, 'mcp')
    render(<ToolsPage initialTab={'nope' as never} />)
    expect(screen.getByRole('tab', { name: /MCP 系统/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('记忆值合法时重开页面回到上次子模块', () => {
    localStorage.setItem(TOOLS_TAB_STORAGE_KEY, 'computer-use')
    render(<ToolsPage />)
    expect(screen.getByRole('tab', { name: /Computer Use/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('非法 initialTab = 已迁走的 lines ⇒ 回落，不会渲染主线档案', () => {
    render(<ToolsPage initialTab={'lines' as never} />)
    expect(screen.getByRole('tab', { name: /工具集/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: /主线管理/ })).toBeNull()
  })

  it('Tab 条提供无障碍语义（tablist + 每项 title 提示）', () => {
    render(<ToolsPage />)
    expect(screen.getByRole('tablist', { name: '工具调用子模块' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /工具集/ })).toHaveAttribute('title', TOOLS_TABS[0].hint)
  })
})
