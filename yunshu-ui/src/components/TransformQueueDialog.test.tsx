/**
 * 转化队列：单击高亮、双击打开操作面板（2026-11-07）。
 *
 * 交互约定（与产品口径一致）：
 *   - **单击** = 选中（只是"我在看这条"，不触发任何写操作）；
 *   - **双击** = 打开操作面板（把"这条现在能做什么"集中一处，按钮复用既有登记/启用流）。
 * 为什么不是单击即弹窗：队列里点错一下就会弹面板，且原有行内按钮仍然存在，
 * 单击即弹会让人以为"点一下就登记了"。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('./ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import TransformQueueDialog from './TransformQueueDialog'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  vi.unstubAllGlobals()
})

/** 队列数据走**裸 fetch**（load() 里就是 fetch(SKILLS_MGMT_TRANSFORMS)），故这里 stub fetch。 */
function renderWithJob() {
  const payload = {
    ok: true, broken: 0, items: [],
    jobs: [{
      job_id: 'job-1', source: 'ingestion', source_ref: 'path:C:/skills/demo',
      form: 'tool', title: '演示工具', status: 'candidate',
      target: { kind: 'tool', id: 'demo_tool' }, artifacts: 'agent/tools/demo.py',
      updated_at: '2026-11-07T10:00:00+08:00',
    }],
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => payload,
  })))
  render(<TransformQueueDialog embedded />)
}

describe('TransformQueueDialog 行交互', () => {
  it('单击选中并高亮', async () => {
    renderWithJob()
    const code = await screen.findByText('job-1')
    const li = code.closest('li') as HTMLLIElement
    expect(li.className).not.toContain('ring-cyan')
    fireEvent.click(li)
    expect(li.className).toContain('ring-cyan')
  })

  it('双击打开操作面板：有详情与「登记为工具」，且不直接写库', async () => {
    renderWithJob()
    const code = await screen.findByText('job-1')
    const li = code.closest('li') as HTMLLIElement
    fireEvent.doubleClick(li)

    // 面板里出现作业详情（行内也有一枚同名按钮，故用 allBy）
    expect(await screen.findByText('tool:demo_tool')).toBeTruthy()
    expect(screen.getByText('agent/tools/demo.py')).toBeTruthy()
    expect(screen.getAllByText('登记为工具').length).toBeGreaterThan(1)
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('关闭'))
    expect(screen.queryByText('tool:demo_tool')).toBeNull()
  })

  it('点面板里的「登记为工具」进入既有登记确认流（不直接提交）', async () => {
    renderWithJob()
    const code = await screen.findByText('job-1')
    fireEvent.doubleClick(code.closest('li') as HTMLLIElement)
    const buttons = await screen.findAllByText('登记为工具')
    fireEvent.click(buttons[buttons.length - 1])
    // 进入既有登记确认流（tool 形态的说明文案），且**没有**直接提交
    expect(await screen.findByText(/把骨架的/)).toBeTruthy()
    expect(screen.queryByText('tool:demo_tool')).toBeNull()   // 操作面板已让位给确认块
    expect(api.hubPost).not.toHaveBeenCalled()
  })
})
