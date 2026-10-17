/**
 * 实例管理 —— LLM 实例与搜索实例（9 条端点）
 * ------------------------------------------------------------------
 * 锁：新建带 {instance}；设为默认 / 测试连通各自 POST；删除需两次点击
 * （删掉默认实例会让运行时无可用服务）；详情走 GET，保存走 PUT。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn(), hubPut: vi.fn(), hubDelete: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
  hubPut: api.hubPut,
  hubDelete: api.hubDelete,
}))

import InstancesPanel from './instances'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  api.hubPut.mockReset()
  api.hubDelete.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/llm/instances') return { ok: true, instances: [{ id: 'llm1', name: 'DeepSeek', provider: 'deepseek', model: 'v3', enabled: true, is_default: true }] }
    if (url === '/api/search/instances') return { ok: true, instances: [{ id: 's1', name: 'DuckDuckGo', engine_type: 'duckduckgo', enabled: true, is_default: false }] }
    if (url === '/api/llm/instances/llm1') return { id: 'llm1', instance: { name: 'DeepSeek', provider: 'deepseek' } }
    return { ok: true }
  })
}

describe('InstancesPanel', () => {
  it('两类实例都拉，默认与启用状态上屏', async () => {
    gets()
    render(<InstancesPanel />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/llm/instances'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/search/instances'))
    expect(await screen.findByText('DeepSeek')).toBeTruthy()
    expect(screen.getByText('默认')).toBeTruthy()
  })

  it('新建 LLM 实例带 {instance}，名称与类型必填', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    const create = screen.getByRole('button', { name: '新建实例' }) as HTMLButtonElement
    expect(create.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('实例名称'), { target: { value: 'Ollama' } })
    fireEvent.change(screen.getByLabelText('实例类型'), { target: { value: 'ollama' } })
    expect(create.disabled).toBe(false)
    fireEvent.click(create)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/llm/instances', {
      instance: { name: 'Ollama', provider: 'ollama', model: '', base_url: '', api_key: '', timeout: 30, enabled: true },
    }))
  })

  it('设为默认与测试连通各打对应 POST', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, success: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('button', { name: '测试连通 llm1' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/llm/instances/llm1/test', {}))

    // 搜索实例在另一个 Tab 里：先切过去，再点它那一行
    fireEvent.click(screen.getByRole('button', { name: '搜索实例' }))
    await screen.findByText('DuckDuckGo')
    fireEvent.click(screen.getByRole('button', { name: '设为默认 s1' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/search/instances/s1/default', {}))
  })

  it('删除要两步：展开确认时不发请求', async () => {
    gets()
    api.hubDelete.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('button', { name: '删除实例 llm1' }))
    expect(await screen.findByText(/它当前是默认实例/)).toBeTruthy()
    expect(api.hubDelete, '展开确认条时不该已经发请求').not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认删除 llm1' }))
    await waitFor(() => expect(api.hubDelete).toHaveBeenCalledWith('/api/llm/instances/llm1'))
  })

  it('详情打开后保存走 PUT，坏 JSON 不发请求', async () => {
    gets()
    api.hubPut.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('button', { name: '实例详情 llm1' }))
    const box = await screen.findByLabelText('实例详情 JSON')
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/llm/instances/llm1'))

    fireEvent.change(box, { target: { value: '{oops' } })
    fireEvent.click(screen.getByRole('button', { name: '保存实例详情' }))
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPut).not.toHaveBeenCalled()

    fireEvent.change(box, { target: { value: '{"instance":{"name":"X"}}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存实例详情' }))
    await waitFor(() => expect(api.hubPut).toHaveBeenCalledWith('/api/llm/instances/llm1', {
      instance: { name: 'X' },
    }))
  })
})
