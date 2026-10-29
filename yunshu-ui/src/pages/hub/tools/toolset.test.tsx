/**
 * 工具集 · 「近 7 日调用」列（2026-10-29）
 * ------------------------------------------------------------------
 * 背景：此前只有技能级台账，工具被调用几次答不出（§11.17-5）。
 * 现在工具调用写在同一条台账的 stage=tool 段，本页读
 * GET /api/skills-mgmt/usage/stats?days=7 的 `tools` 段展示。
 *
 * 锁死的口径：
 *   - 有记录 ⇒ 显示「N 次」；有失败 ⇒ 「N 次（失败 M）」（琥珀色 + 悬浮最近错误）；
 *   - **没有记录 ≠ 调用 0 次**：从未调用/旧后端/接口失败一律显示 '-'（不假装 0）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ getEnvelope: vi.fn(), hubGet: vi.fn() }))

vi.mock('@/api/envelope', () => ({ getEnvelope: mocks.getEnvelope }))
vi.mock('../components/ui', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../components/ui')>()
  return { ...orig, hubGet: mocks.hubGet }
})

const { default: ToolsToolset } = await import('./toolset')

const TOOLS = [
  { name: 'read_file', enabled: true },
  { name: 'shell_execute', enabled: true },
  { name: 'never_called', enabled: true },
]

function routeHubGet(payload: { usage?: unknown; planes?: unknown }) {
  mocks.hubGet.mockImplementation(async (url: string) => {
    if (String(url).includes('usage/stats')) return payload.usage ?? { tools: {} }
    return payload.planes ?? { tools: [], planes: [] }
  })
}

describe('工具集 · 近 7 日调用列', () => {
  beforeEach(() => {
    mocks.getEnvelope.mockReset()
    mocks.hubGet.mockReset()
    mocks.getEnvelope.mockResolvedValue(TOOLS)
  })
  afterEach(cleanup)

  it('有记录显示次数，失败单独标出，无记录显示 -（不假装 0 次）', async () => {
    routeHubGet({
      usage: {
        tools: {
          read_file: { calls: 3, ok: 3, fail: 0 },
          shell_execute: { calls: 2, ok: 1, fail: 1, last_error: 'ToolError: 找不到命令' },
        },
      },
    })
    render(<ToolsToolset />)

    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())
    expect(screen.getByText('近 7 日调用')).toBeInTheDocument()
    expect(screen.getByText('3 次')).toBeInTheDocument()
    const failedCell = screen.getByText('2 次（失败 1）')
    expect(failedCell.getAttribute('title')).toContain('找不到命令')

    const neverRow = screen.getByText('never_called').closest('tr')!
    expect(within(neverRow).getAllByText('-').length).toBeGreaterThan(0)
    expect(within(neverRow).queryByText(/次/)).toBeNull()
  })

  it('台账接口失败时整列降级为 -，页面不报错', async () => {
    mocks.hubGet.mockImplementation(async (url: string) => {
      if (String(url).includes('usage/stats')) throw new Error('台账不可用')
      return { tools: [], planes: [] }
    })
    render(<ToolsToolset />)

    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())
    expect(screen.queryByText(/次（失败/)).toBeNull()
    expect(screen.queryByText(/^\d+ 次$/)).toBeNull()
  })
})
