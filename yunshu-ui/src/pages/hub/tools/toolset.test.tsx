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
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ getEnvelope: vi.fn(), hubGet: vi.fn(), hubPost: vi.fn() }))

vi.mock('@/api/envelope', () => ({ getEnvelope: mocks.getEnvelope }))
vi.mock('../components/ui', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../components/ui')>()
  return { ...orig, hubGet: mocks.hubGet, hubPost: mocks.hubPost }
})

const { default: ToolsToolset } = await import('./toolset')
// 三张表共用的按钮常量：用例断言「页面真的用了这一份」，而不是各写一套
const actions = await import('@/lib/tableActions')

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
    mocks.hubPost.mockReset()
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

  it('工具表有独立「说明」列（与技能中心/工作流同口径，不再挤在名字底下）', async () => {
    mocks.getEnvelope.mockResolvedValue([
      { name: 'read_file', enabled: true, description: '读取文件内容（支持多段范围）' },
    ])
    routeHubGet({})
    render(<ToolsToolset />)

    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())
    expect(screen.getByText('说明')).toBeInTheDocument()
    const cell = screen.getByText('读取文件内容（支持多段范围）')
    expect(cell.className).toContain('line-clamp-3')
    expect(cell.className).toContain('wrap-break-word')
    // 名字列不再重复渲染描述：全文只出现一次
    expect(screen.getAllByText('读取文件内容（支持多段范围）')).toHaveLength(1)
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

describe('工具集 · 移除运行时工具（2026-11-07）', () => {
  beforeEach(() => {
    mocks.getEnvelope.mockReset()
    mocks.hubGet.mockReset()
    mocks.hubPost.mockReset()
    routeHubGet({})
  })
  afterEach(cleanup)

  it('只有运行时工具显示「移除」；内建工具不给按钮（需走 PR 改代码）', async () => {
    mocks.getEnvelope.mockResolvedValue([
      { name: 'byted_seedance_video_generate', enabled: false, runtime: true, removable: true },
      { name: 'read_file', enabled: true, runtime: false, removable: false },
    ])
    render(<ToolsToolset />)
    await waitFor(() => expect(screen.getByText('byted_seedance_video_generate')).toBeInTheDocument())
    const btns = screen.getAllByRole('button', { name: '移除' })
    expect(btns).toHaveLength(1)
    const row = screen.getByText('read_file').closest('tr')!
    expect(within(row).queryAllByRole('button', { name: '移除' })).toHaveLength(0)
  })

  it('移除流程：先 dry_run 预览（列出将删文件），再 confirm 落盘', async () => {
    mocks.getEnvelope.mockResolvedValue([
      { name: 'byted_seedance_video_generate', enabled: false, runtime: true, removable: true },
    ])
    mocks.hubPost.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      if (String(url).includes('remove-runtime')) {
        if (body.dry_run) {
          return { ok: true, dry_run: true, files: ['agent/tools/custom/x.py', 'data/dynamic_tools.json'],
                   in_manifest: false, note: '不在入库声明口径' }
        }
        return { ok: true, dry_run: false, backup: 'data/backups/tool-remove-x' }
      }
      return { ok: true }
    })
    render(<ToolsToolset />)
    fireEvent.click(await screen.findByRole('button', { name: '移除' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('agent/tools/custom/x.py')
    expect(mocks.hubPost).toHaveBeenCalledWith(
      expect.stringContaining('remove-runtime'),
      expect.objectContaining({ name: 'byted_seedance_video_generate', dry_run: true }),
    )

    fireEvent.click(within(dialog).getByRole('button', { name: '移除' }))
    await waitFor(() => expect(mocks.hubPost).toHaveBeenCalledWith(
      expect.stringContaining('remove-runtime'),
      expect.objectContaining({ name: 'byted_seedance_video_generate', dry_run: false, confirm: true }),
    ))
  })
})

describe('工具集 · 深链定位（?tool=）', () => {
  it('连字符写法也能定位到注册表里的下划线工具（不许卡在「正在定位」）', async () => {
    // jsdom 没有 scrollIntoView；不补会抛 TypeError
    const origScroll = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = vi.fn()
    window.history.replaceState(null, '', '/chat#/workbench?panel=tools&tool=byted-seedance-video-generate')
    mocks.getEnvelope.mockResolvedValue([
      { name: 'byted_seedance_video_generate', enabled: false },
      { name: 'read_file', enabled: true },
    ])
    routeHubGet({ planes: {
      tools: [{ name: 'byted_seedance_video_generate', plane: 'act' }],
      planes: [{ key: 'act', label: '行动', hint: '会改变世界', count: 1 }],
    } })

    render(<ToolsToolset />)

    // 行真的被打上标记（此前 row.name === focusTool 恒不等 ⇒ 永远没有这个属性）
    await waitFor(() => {
      expect(document.querySelector('[data-focus-tool="byted_seedance_video_generate"]')).not.toBeNull()
    })
    // 横幅必须从「正在定位」翻成「已定位到」
    await waitFor(() => expect(screen.getByText(/已定位到/)).toBeInTheDocument())
    expect(screen.queryByText(/正在定位/)).toBeNull()

    Element.prototype.scrollIntoView = origScroll
    window.history.replaceState(null, '', '/')
  })

  it('真没有这个工具 ⇒ 明确说「未找到工具」，不留在「正在定位」', async () => {
    window.history.replaceState(null, '', '/chat#/workbench?panel=tools&tool=no-such-tool-at-all')
    mocks.getEnvelope.mockResolvedValue([{ name: 'read_file', enabled: true }])
    routeHubGet({ planes: { tools: [], planes: [] } })

    render(<ToolsToolset />)

    await waitFor(() => expect(screen.getByText(/未找到工具/)).toBeInTheDocument())
    expect(screen.queryByText(/正在定位/)).toBeNull()
    window.history.replaceState(null, '', '/')
  })
})
describe('工具集 · 列宽可拖 + 按钮与技能中心同口径（2026-11-08）', () => {
  beforeEach(() => {
    mocks.getEnvelope.mockReset()
    mocks.hubGet.mockReset()
    mocks.hubPost.mockReset()
    routeHubGet({})
  })
  afterEach(cleanup)

  it('表头每个内容列都有拖动手柄，表是 table-fixed + colgroup（与技能中心同一实现）', async () => {
    mocks.getEnvelope.mockResolvedValue([{ name: 'read_file', enabled: true }])
    const { container } = render(<ToolsToolset />)
    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())

    // 工具表 8 列 ⇒ 8 个手柄（技能中心/工作流用的是同一个 components/ColResizer）
    expect(screen.getAllByRole('separator', { name: '拖动调整列宽' })).toHaveLength(8)
    const table = container.querySelector('table')!
    expect(table.className).toContain('table-fixed')
    const cols = table.querySelectorAll('colgroup > col')
    expect(cols.length).toBe(8)
    // 默认宽度来自模块常量（TOOL_COL_WIDTHS.name = 240）
    expect((cols[0] as HTMLElement).style.width).toBe('240px')
  })

  it('行内按钮用 @/lib/tableActions 的四个常量（形状一致、颜色表语义）', async () => {
    mocks.getEnvelope.mockResolvedValue([
      { name: 'read_file', enabled: true },
      { name: 'byted_x', enabled: false, runtime: true, removable: true },
    ])
    render(<ToolsToolset />)
    await waitFor(() => expect(screen.getByText('read_file')).toBeInTheDocument())

    const enabledRow = screen.getByText('read_file').closest('tr')!
    // 已启用 ⇒ 动作是「停用」⇒ 中性色
    expect(within(enabledRow).getByRole('button', { name: '停用' }).className).toBe(actions.TABLE_BTN)
    const disabledRow = screen.getByText('byted_x').closest('tr')!
    // 未启用 ⇒ 动作是「启用」⇒ 主动作色
    expect(within(disabledRow).getByRole('button', { name: '启用' }).className).toBe(actions.TABLE_BTN_PRIMARY)
    // 移除 = 破坏性
    expect(within(disabledRow).getByRole('button', { name: '移除' }).className).toBe(actions.TABLE_BTN_DANGER)
  })
})
