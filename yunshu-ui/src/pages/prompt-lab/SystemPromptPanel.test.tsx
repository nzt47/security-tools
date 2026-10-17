/**
 * 系统提示词 —— 3 条端点
 * 锁：读模板与同步状态；保存把后端"未知占位符"错误原样上屏；重置需确认。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../hub/components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import SystemPromptPanel from './SystemPromptPanel'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/system-prompt') {
      return {
        template: '你是「云枢」。日期 {current_date}',
        is_custom: true,
        is_default: false,
        placeholders: { current_date: '当前日期（自动填充）', body_status: '身体状态描述' },
        preview: '你是「云枢」。日期 2026年10月18日',
      }
    }
    if (url === '/api/system-prompt/sync-status') return { is_synced: true, config_template_length: 20, runtime_template_length: 20 }
    return {}
  })
}

describe('SystemPromptPanel', () => {
  it('读模板 + 同步状态，占位符可点击插入', async () => {
    gets()
    render(<SystemPromptPanel />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/system-prompt'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/system-prompt/sync-status'))
    const box = (await screen.findByLabelText('系统提示词模板')) as HTMLTextAreaElement
    expect(box.value).toContain('你是「云枢」')

    fireEvent.click(screen.getByText('{body_status}'))
    await waitFor(() => expect((screen.getByLabelText('系统提示词模板') as HTMLTextAreaElement).value).toContain('{body_status}'))
  })

  it('保存把后端的「未知占位符」错误原样上屏', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: false, error: '模板中包含未知占位符: body_state' })
    render(<SystemPromptPanel />)
    await screen.findByLabelText('系统提示词模板')

    fireEvent.change(screen.getByLabelText('系统提示词模板'), { target: { value: '坏模板 {body_state}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存系统提示词' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/system-prompt', { content: '坏模板 {body_state}' }))
    expect(await screen.findByText(/未知占位符: body_state/)).toBeTruthy()
  })

  it('重置需勾确认', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SystemPromptPanel />)
    await screen.findByLabelText('系统提示词模板')

    const reset = screen.getByRole('button', { name: '重置系统提示词' }) as HTMLButtonElement
    expect(reset.disabled, '重置会丢掉整段自定义提示词').toBe(true)
    fireEvent.click(screen.getByLabelText('确认重置模板'))
    fireEvent.click(reset)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/system-prompt/reset', {}))
  })
})
