/**
 * Computer Use 分批接入测试（browser/screenshot、browser/close、clipboard）
 * 锁死：三个端点真的被调用，且成功/失败态上屏。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import ToolsComputerUse from './computer-use'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

describe('ToolsComputerUse 分批接入', () => {
  it('截取页面：调用 BROWSER_SCREENSHOT 并渲染截图', async () => {
    api.hubGet.mockResolvedValue({ ok: true, screenshot_base64: 'AAAA' })
    render(<ToolsComputerUse />)
    fireEvent.click(screen.getByRole('button', { name: /截取页面/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/browser/screenshot'))
    const img = await screen.findByTestId('browser-screenshot')
    expect(img.querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,AAAA')
  })

  it('截图失败（ok=false）：上屏错误且不渲染截图', async () => {
    api.hubGet.mockResolvedValue({ ok: false, error: '浏览器不可用' })
    render(<ToolsComputerUse />)
    fireEvent.click(screen.getByRole('button', { name: /截取页面/ }))
    expect(await screen.findByText(/截图失败：浏览器不可用/)).toBeTruthy()
    expect(screen.queryByTestId('browser-screenshot')).toBeNull()
  })

  it('关闭浏览器：POST BROWSER_CLOSE', async () => {
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ToolsComputerUse />)
    fireEvent.click(screen.getByRole('button', { name: /关闭浏览器/ }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/browser/close', {}))
  })

  it('剪贴板：读取 GET /api/clipboard；写入 POST /api/clipboard', async () => {
    api.hubGet.mockResolvedValue({ ok: true, content: '剪贴板里的字' })
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ToolsComputerUse />)

    fireEvent.click(screen.getByRole('button', { name: /读取剪贴板/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/clipboard'))
    expect(await screen.findByText('剪贴板里的字')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('剪贴板写入内容'), { target: { value: '要写进去' } })
    fireEvent.click(screen.getByRole('button', { name: /写入剪贴板/ }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/clipboard', { text: '要写进去' }))
    expect(await screen.findByText('已写入剪贴板')).toBeTruthy()
  })
})
