/**
 * 工具治理 —— 6 条端点
 * 锁：分类/健康/状态三面并发；加/改名/删关键词各走对方法与 body；重置需确认。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn(), hubDeleteJson: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
  hubDeleteJson: api.hubDeleteJson,
}))

import ToolsGovernance from './governance'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  api.hubDeleteJson.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/tools/categories') {
      return {
        categories: [{ key: 'core', label: '核心工具', icon: '⚙', always: true, tools: ['get_status'], description: '始终发送' }],
        keywords: { core: ['状态', '自检'] },
      }
    }
    if (url === '/api/tools/health') return { total_tools: 91, healthy_tools: 90, overall_score: 99 }
    if (url === '/api/tools/status-batch') return [{ name: 'get_status', enabled: true }]
    return {}
  })
}

describe('ToolsGovernance', () => {
  it('三面并发拉取并渲染分类与关键词', async () => {
    gets()
    render(<ToolsGovernance />)
    for (const u of ['/api/tools/categories', '/api/tools/health', '/api/tools/status-batch']) {
      await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith(u))
    }
    // 「核心工具」同时出现在分类卡片与分类下拉的 option 里 —— 按"至少一处"断言
    await waitFor(() => expect(screen.getAllByText(/核心工具/).length).toBeGreaterThan(0))
    expect(screen.getByText('状态')).toBeTruthy()
  })

  it('加关键词 POST {category, keyword}；改名 POST update', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ToolsGovernance />)
    await waitFor(() => expect(screen.getAllByText(/核心工具/).length).toBeGreaterThan(0))

    fireEvent.change(screen.getByLabelText('新关键词'), { target: { value: '健康' } })
    fireEvent.click(screen.getByRole('button', { name: '添加关键词' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/tools/keywords', { category: 'core', keyword: '健康' }))

    fireEvent.change(screen.getByLabelText('原关键词'), { target: { value: '状态' } })
    fireEvent.change(screen.getByLabelText('改名后的关键词'), { target: { value: '运行状态' } })
    fireEvent.click(screen.getByRole('button', { name: '修改关键词' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/tools/keywords/update', {
      category: 'core', old_keyword: '状态', new_keyword: '运行状态',
    }))
  })

  it('删关键词必须带 body（hubDeleteJson），不能用只发 URL 的 DELETE', async () => {
    gets()
    api.hubDeleteJson.mockResolvedValue({ ok: true })
    render(<ToolsGovernance />)
    await screen.findByText('自检')

    fireEvent.click(screen.getByRole('button', { name: '删除关键词 自检' }))
    await waitFor(() => expect(api.hubDeleteJson).toHaveBeenCalledWith('/api/tools/keywords', {
      category: 'core', keyword: '自检',
    }))
  })

  it('重置关键词需勾确认', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ToolsGovernance />)
    await waitFor(() => expect(screen.getAllByText(/核心工具/).length).toBeGreaterThan(0))

    const reset = screen.getByRole('button', { name: '重置关键词' }) as HTMLButtonElement
    expect(reset.disabled, '重置会丢掉所有自定义关键词，必须先确认').toBe(true)
    fireEvent.click(screen.getByLabelText('确认重置关键词'))
    fireEvent.click(reset)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/tools/keywords/reset', {}))
  })
})
