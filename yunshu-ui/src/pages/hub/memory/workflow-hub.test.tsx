/** 工作流叶子页 —— 工作流技能 / 可视化编辑两个 Tab，且 Tab 可深链（?panel=workflow&tab=visual） */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('./workflow', () => ({ MemoryWorkflowTable: () => <div>WF_TABLE</div> }))
vi.mock('./workflow-visual', () => ({ default: () => <div>WF_VISUAL</div> }))
vi.mock('./ConvertibleWorkflows', () => ({ default: () => <div>CONVERTIBLE</div> }))

import WorkflowHub, { tabFromSearch } from './workflow-hub'

beforeEach(() => { window.location.hash = '#/workbench?panel=workflow' })
afterEach(() => { cleanup() })

describe('WorkflowHub（工作流叶子）', () => {
  it('默认落在「工作流技能」，并带上可转换工作流', () => {
    render(<WorkflowHub />)
    expect(screen.getByText('WF_TABLE')).toBeTruthy()
    expect(screen.getByText('CONVERTIBLE')).toBeTruthy()
    expect(screen.queryByText('WF_VISUAL')).toBeNull()
  })

  it('切到「可视化编辑」：内容切换且 URL 写入 tab=visual（可深链/刷新保持）', () => {
    render(<WorkflowHub />)
    fireEvent.click(screen.getByRole('button', { name: '可视化编辑' }))
    expect(screen.getByText('WF_VISUAL')).toBeTruthy()
    expect(window.location.hash).toContain('panel=workflow')
    expect(window.location.hash).toContain('tab=visual')
  })

  it('从 URL 进来自动落在可视化编辑（刷新/分享链接场景）', () => {
    window.location.hash = '#/workbench?panel=workflow&tab=visual'
    render(<WorkflowHub />)
    expect(screen.getByText('WF_VISUAL')).toBeTruthy()
    expect(screen.queryByText('WF_TABLE')).toBeNull()
  })

  it('tabFromSearch 只认识 visual，其余一律回落 skills', () => {
    expect(tabFromSearch('?panel=workflow&tab=visual')).toBe('visual')
    expect(tabFromSearch('?tab=skills')).toBe('skills')
    expect(tabFromSearch('?tab=bogus')).toBe('skills')
    expect(tabFromSearch('')).toBe('skills')
  })
})
