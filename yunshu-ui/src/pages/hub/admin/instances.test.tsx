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
    if (url === '/api/llm/instances') return { ok: true, instances: [{ id: 'llm1', name: 'DeepSeek', provider: 'deepseek', model: 'v3', api_endpoint: 'https://api.deepseek.com/v1', timeout: 30, enabled: true, is_default: true }] }
    if (url === '/api/search/instances') return { ok: true, instances: [{ id: 's1', name: 'DuckDuckGo', engine_type: 'duckduckgo', timeout: 30, enabled: true, is_default: false }] }
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

  it('新建 LLM 实例带 {instance}；api_endpoint 必填（缺了不提交）', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    const create = screen.getByRole('button', { name: '新建实例' }) as HTMLButtonElement
    expect(create.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('实例名称'), { target: { value: 'Ollama' } })
    fireEvent.change(screen.getByLabelText('实例类型'), { target: { value: 'ollama' } })
    // 后端 validate_llm_instance 要求 api_endpoint；缺了就 400，前端先挡住
    expect(create.disabled, 'LLM 实例缺 api_endpoint 时不允许提交').toBe(true)
    fireEvent.change(screen.getByLabelText('接口端点'), { target: { value: 'http://127.0.0.1:11434/v1' } })
    expect(create.disabled).toBe(false)
    fireEvent.click(create)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/llm/instances', {
      // 字段名必须是 api_endpoint（旧写法 base_url 必被后端 400 拒绝）
      instance: { name: 'Ollama', provider: 'ollama', model: '', api_endpoint: 'http://127.0.0.1:11434/v1', api_key: '', timeout: 30, enabled: true },
    }))
  })

  it('搜索实例：内置引擎不必填端点；custom 必填并随请求发 api_endpoint', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('tab', { name: '搜索实例' }))
    // 引擎类型是下拉（不是自由文本）：后端按精确小写词表校验，键盘输入 "Tavily" 只会得到 400
    const engineOptions = Array.from((screen.getByTestId('search-engine-type') as HTMLSelectElement).options).map((o) => o.value)
    expect(engineOptions).toEqual(expect.arrayContaining(['duckduckgo', 'tavily', 'custom']))
    expect(engineOptions, '下拉值必须全小写（与 BUILTIN_ENGINES 对齐）').not.toContain('Tavily')
    const create = screen.getByRole('button', { name: '新建实例' }) as HTMLButtonElement
    fireEvent.change(screen.getByLabelText('实例名称'), { target: { value: 'DDG' } })
    fireEvent.change(screen.getByLabelText('实例类型'), { target: { value: 'duckduckgo' } })
    expect(create.disabled, '内置引擎不需要 api_endpoint').toBe(false)

    fireEvent.change(screen.getByLabelText('实例类型'), { target: { value: 'custom' } })
    expect(create.disabled, 'custom 引擎缺 api_endpoint 时不允许提交').toBe(true)
    fireEvent.change(screen.getByLabelText('搜索接口端点'), { target: { value: 'https://example.com/search?q={query}' } })
    expect(create.disabled).toBe(false)
    fireEvent.click(create)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/search/instances', {
      instance: {
        name: 'DDG', engine_type: 'custom', api_key: '',
        api_endpoint: 'https://example.com/search?q={query}', timeout: 30, enabled: true,
      },
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
    fireEvent.click(screen.getByRole('tab', { name: '搜索实例' }))
    // 注意：不能按文本找「DuckDuckGo」—— 下方 engine_type 下拉的 option 也是同名文本，
    // getByText 会命中多个元素。按行内动作按钮定位更稳。
    await screen.findByRole('button', { name: '设为默认 s1' })
    fireEvent.click(screen.getByRole('button', { name: '设为默认 s1' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/search/instances/s1/default', {}))
  })

  it('点「编辑」把已有 LLM 实例填进表单；保存走 PUT（api_key 留空不发送）', async () => {
    gets()
    api.hubPut.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('button', { name: '编辑实例 llm1' }))
    expect((screen.getByLabelText('实例名称') as HTMLInputElement).value).toBe('DeepSeek')
    expect((screen.getByLabelText('实例类型') as HTMLInputElement).value).toBe('deepseek')
    expect((screen.getByLabelText('模型名') as HTMLInputElement).value).toBe('v3')
    expect((screen.getByTestId('llm-endpoint') as HTMLInputElement).value).toBe('https://api.deepseek.com/v1')
    expect((screen.getByLabelText('实例超时秒') as HTMLInputElement).value).toBe('30')
    const save = screen.getByTestId('instance-save')
    expect(save.textContent).toContain('保存修改')

    fireEvent.change(screen.getByLabelText('实例名称'), { target: { value: 'DeepSeek 主实例' } })
    fireEvent.click(save)
    await waitFor(() => expect(api.hubPut).toHaveBeenCalledWith('/api/llm/instances/llm1', {
      updates: {
        name: 'DeepSeek 主实例', provider: 'deepseek', model: 'v3',
        api_endpoint: 'https://api.deepseek.com/v1', timeout: 30,
      },
    }))
    expect((api.hubPut.mock.calls[0][1] as { updates: Record<string, unknown> }).updates).not.toHaveProperty('api_key')
  })

  it('搜索实例同样可编辑：预填引擎类型/名称，保存走 PUT', async () => {
    gets()
    api.hubPut.mockResolvedValue({ ok: true })
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('tab', { name: '搜索实例' }))
    await screen.findByRole('button', { name: '设为默认 s1' })
    fireEvent.click(screen.getByRole('button', { name: '编辑实例 s1' }))
    expect((screen.getByLabelText('实例名称') as HTMLInputElement).value).toBe('DuckDuckGo')
    expect((screen.getByTestId('search-engine-type') as HTMLSelectElement).value).toBe('duckduckgo')

    fireEvent.change(screen.getByLabelText('实例名称'), { target: { value: 'DDG 主实例' } })
    fireEvent.click(screen.getByTestId('instance-save'))
    await waitFor(() => expect(api.hubPut).toHaveBeenCalledWith('/api/search/instances/s1', {
      updates: { name: 'DDG 主实例', engine_type: 'duckduckgo', timeout: 30 },
    }))
  })

  it('取消编辑清空表单并回到新建模式', async () => {
    gets()
    render(<InstancesPanel />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('button', { name: '编辑实例 llm1' }))
    expect((screen.getByLabelText('实例名称') as HTMLInputElement).value).toBe('DeepSeek')
    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }))
    expect((screen.getByLabelText('实例名称') as HTMLInputElement).value).toBe('')
    expect(screen.getByRole('button', { name: '新建实例' })).toBeTruthy()
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