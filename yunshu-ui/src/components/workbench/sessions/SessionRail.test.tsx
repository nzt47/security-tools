/**
 * SessionRail 测试 —— 三段树（项目 → 分组 → 会话）的项目侧
 * ------------------------------------------------------------------
 * 覆盖：
 *   - 顶部项目选择条：列出已建项目 + 「未归属项目」，切换回调父层；
 *   - 选中项目只显示该项目下的会话（未归属视图只显示无项目会话）；
 *   - 会话行菜单「移动到项目」调用 onMoveProject（null = 未归属项目）；
 *   - 「新建项目」弹窗：项目名 + 工作空间绝对路径 + 自动创建，成功后自动选中。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SessionRail } from './SessionRail'
import type { Project, SessionMeta } from '../../../lib/sessionApi'

const P1: Project = { id: 'proj-1', name: '项目甲', workspace_root: 'C:/work/alpha' }
const P2: Project = { id: 'proj-2', name: '项目乙', workspace_root: 'C:/work/beta' }

function makeProps(over: Record<string, unknown> = {}) {
  return {
    sessions: [] as SessionMeta[],
    activeId: null,
    groups: [],
    membership: {} as Record<string, string>,
    onSelect: vi.fn(),
    onCreate: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    onOpenWorkspace: vi.fn(),
    onCreateGroup: vi.fn(),
    onRenameGroup: vi.fn(),
    onDeleteGroup: vi.fn(),
    onAssignGroup: vi.fn(),
    ...over,
  }
}

const SESSIONS: SessionMeta[] = [
  { id: 's-a', title: '会话 A', project_id: 'proj-1' },
  { id: 's-b', title: '会话 B', project_id: 'proj-2' },
  { id: 's-none', title: '会话 N' },
]

describe('SessionRail · 项目选择条', () => {
  afterEach(() => cleanup())

  it('列出已建项目与「未归属项目」，切换时回调父层', () => {
    const onSelectProject = vi.fn()
    render(<SessionRail {...makeProps({ projects: [P1, P2], onSelectProject })} />)

    const select = screen.getByLabelText('项目') as HTMLSelectElement
    expect(within(select).getByRole('option', { name: '未归属项目' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: '项目甲' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: '项目乙' })).toBeInTheDocument()

    fireEvent.change(select, { target: { value: 'proj-2' } })
    expect(onSelectProject).toHaveBeenCalledWith('proj-2')
    fireEvent.change(select, { target: { value: '' } })
    expect(onSelectProject).toHaveBeenLastCalledWith(null)
  })

  it('选中项目时只显示该项目下的会话', () => {
    const { container } = render(
      <SessionRail {...makeProps({ projects: [P1, P2], sessions: SESSIONS, activeProjectId: 'proj-1' })} />,
    )
    expect(container.querySelector('[data-session-id="s-a"]')).not.toBeNull()
    expect(container.querySelector('[data-session-id="s-b"]')).toBeNull()
    expect(container.querySelector('[data-session-id="s-none"]')).toBeNull()
  })

  it('「未归属项目」视图只显示无项目会话', () => {
    const { container } = render(
      <SessionRail {...makeProps({ projects: [P1, P2], sessions: SESSIONS, activeProjectId: null })} />,
    )
    expect(container.querySelector('[data-session-id="s-none"]')).not.toBeNull()
    expect(container.querySelector('[data-session-id="s-a"]')).toBeNull()
    expect(container.querySelector('[data-session-id="s-b"]')).toBeNull()
  })

  it('会话行菜单「移动到项目」调用 onMoveProject（含未归属项目）', () => {
    const onMoveProject = vi.fn()
    const { container } = render(
      <SessionRail
        {...makeProps({ projects: [P1, P2], sessions: SESSIONS, activeProjectId: null, onMoveProject })}
      />,
    )

    const row = container.querySelector('[data-session-id="s-none"]') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: '移动到分组' }))
    const menu = container.querySelector('[data-move-menu]') as HTMLElement
    expect(menu).not.toBeNull()

    fireEvent.click(within(menu).getByRole('button', { name: /项目甲/ }))
    expect(onMoveProject).toHaveBeenCalledWith('s-none', 'proj-1')

    fireEvent.click(within(row).getByRole('button', { name: '移动到分组' }))
    const menu2 = container.querySelector('[data-move-menu]') as HTMLElement
    fireEvent.click(within(menu2).getByRole('button', { name: '未归属项目' }))
    expect(onMoveProject).toHaveBeenLastCalledWith('s-none', null)
  })

  it('新建项目弹窗：提交 name/path/create，成功后自动选中新项目', async () => {
    const created: Project = { id: 'proj-new', name: '新项目', workspace_root: 'C:/work/new' }
    const onCreateProject = vi.fn(async () => created)
    const onSelectProject = vi.fn()
    render(<SessionRail {...makeProps({ onCreateProject, onSelectProject })} />)

    fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
    expect(screen.getByRole('dialog', { name: '新建项目' })).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('项目名'), { target: { value: '新项目' } })
    fireEvent.change(screen.getByLabelText('工作空间绝对路径'), { target: { value: 'C:\\work\\new' } })
    fireEvent.click(screen.getByLabelText('目录不存在时自动创建'))
    fireEvent.click(screen.getByRole('button', { name: '创建项目' }))

    await waitFor(() =>
      expect(onCreateProject).toHaveBeenCalledWith({
        name: '新项目',
        path: 'C:\\work\\new',
        create: true,
      }),
    )
    await waitFor(() => expect(onSelectProject).toHaveBeenCalledWith('proj-new'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '新建项目' })).toBeNull())
  })

  it('新建项目失败时错误上屏且弹窗保留', async () => {
    const onCreateProject = vi.fn(async () => {
      throw new Error('路径不是本地绝对路径')
    })
    render(<SessionRail {...makeProps({ onCreateProject })} />)

    fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
    fireEvent.change(screen.getByLabelText('项目名'), { target: { value: '坏项目' } })
    fireEvent.change(screen.getByLabelText('工作空间绝对路径'), { target: { value: 'relative/x' } })
    fireEvent.click(screen.getByRole('button', { name: '创建项目' }))

    await waitFor(() => expect(screen.getByText(/路径不是本地绝对路径/)).toBeInTheDocument())
    expect(screen.getByRole('dialog', { name: '新建项目' })).toBeInTheDocument()
  })
})
