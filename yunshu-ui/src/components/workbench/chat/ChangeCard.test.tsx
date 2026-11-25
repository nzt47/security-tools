/**
 * ChangeCard 测试（E1-3b，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住：文件数/增删行/逐文件展开 diff；新增文件没有 diff 时给说明不装作有空补丁；
 * 截断要明说；`scope=workspace` 时明确声明"不保证只含本会话"；没有检查点/没有文件时不渲染。
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ChangeCard } from './ChangeCard'
import type { ChangesState } from '../../../lib/changesApi'

function makeState(patch: Partial<ChangesState> = {}): ChangesState {
  return {
    session_id: 's1',
    scope: 'session',
    checkpoint: {
      id: '20261125T150000Z-abcd1234',
      created_at: '2026-11-25T15:00:00',
      trigger_tool: 'edit',
      session_id: 's1',
    },
    files: [
      {
        path: 'agent/a.py', status: 'M', size: 120, captured: true, binary: false,
        new_file: false, added: 3, removed: 1,
        diff: 'diff --git a/agent/a.py b/agent/a.py\n-old\n+new\n+x\n+y\n', truncated: false,
      },
      {
        path: 'docs/new.md', status: '?', size: 10, captured: true, binary: false,
        new_file: true, added: 0, removed: 0, diff: '', truncated: false,
      },
    ],
    counts: { files: 2, added: 3, removed: 1, truncated: 0 },
    note: '',
    ...patch,
  }
}

afterEach(() => cleanup())

describe('ChangeCard · 本轮变更卡', () => {
  it('没有检查点时不渲染', () => {
    const { container } = render(<ChangeCard changes={makeState({ checkpoint: null, files: [] })} />)
    expect(container.firstChild).toBeNull()
  })

  it('没有文件时不渲染', () => {
    const { container } = render(<ChangeCard changes={makeState({ files: [], counts: { files: 0, added: 0, removed: 0, truncated: 0 } })} />)
    expect(container.firstChild).toBeNull()
  })

  it('列出文件数、增删行与触发工具', () => {
    render(<ChangeCard changes={makeState()} />)
    expect(screen.getByTestId('change-card')).toBeTruthy()
    expect(screen.getByText('2 个文件')).toBeTruthy()
    expect(screen.getByTestId('change-added').textContent).toBe('+3')
    expect(screen.getByTestId('change-removed').textContent).toBe('-1')
    expect(screen.getByText('agent/a.py')).toBeTruthy()
    expect(screen.getByText(/触发：edit/)).toBeTruthy()
  })

  it('展开有 diff 的文件显示补丁', () => {
    render(<ChangeCard changes={makeState()} />)
    fireEvent.click(screen.getByTestId('change-toggle-agent/a.py'))
    const diff = screen.getByTestId('change-diff-agent/a.py')
    expect(diff.textContent).toContain('+new')
  })

  it('新增文件没有 diff 时给出说明（不装作有空补丁）', () => {
    render(<ChangeCard changes={makeState()} />)
    fireEvent.click(screen.getByTestId('change-toggle-docs/new.md'))
    expect(screen.getByText(/新增文件/)).toBeTruthy()
    expect(screen.queryByTestId('change-diff-docs/new.md')).toBeNull()
  })

  it('截断要明说', () => {
    const state = makeState({
      files: [{
        path: 'agent/a.py', status: 'M', size: 1, captured: true, binary: false,
        new_file: false, added: 1, removed: 0, diff: 'x', truncated: true,
      }],
      counts: { files: 1, added: 1, removed: 0, truncated: 1 },
    })
    render(<ChangeCard changes={state} />)
    expect(screen.getByText(/1 个文件的 diff 被截断/)).toBeTruthy()
  })

  it('工作区级快照明确声明范围（不假装属于本会话）', () => {
    render(<ChangeCard changes={makeState({ scope: 'workspace', note: '工作区级快照' })} />)
    expect(screen.getByTestId('change-scope-note')).toBeTruthy()
    expect(screen.getByText('工作区级快照')).toBeTruthy()
  })

  it('会话级快照不显示范围提示', () => {
    render(<ChangeCard changes={makeState()} />)
    expect(screen.queryByTestId('change-scope-note')).toBeNull()
  })
})
