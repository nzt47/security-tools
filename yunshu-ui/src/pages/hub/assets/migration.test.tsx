/**
 * 资产迁移 + 网络迁移 —— 5 条端点
 * 锁：导出是下载链接（不走 JSON 请求）；恢复/删除/导入都必须先勾确认。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn(), hubDelete: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
  hubDelete: api.hubDelete,
}))

import AssetsMigration from '../assets/MigrationPanel'
import NetworkTransfer from '../network/TransferPanel'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  api.hubDelete.mockReset()
})

describe('AssetsMigration', () => {
  it('导出是下载链接；恢复与删除需勾确认', async () => {
    api.hubPost.mockResolvedValue({ ok: true, result: { restored_files: ['prompts'] } })
    api.hubDelete.mockResolvedValue({ ok: true })
    render(<AssetsMigration />)

    const link = screen.getByRole('link', { name: '导出全部资产' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/assets/export')

    const restore = screen.getByRole('button', { name: '恢复资产' }) as HTMLButtonElement
    expect(restore.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('备份 id'), { target: { value: 'bk_1' } })
    fireEvent.click(screen.getByLabelText('确认恢复资产'))
    fireEvent.click(restore)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/assets/restore', { backup_id: 'bk_1' }))

    const del = screen.getByRole('button', { name: '删除备份' }) as HTMLButtonElement
    fireEvent.click(screen.getByLabelText('确认删除备份'))
    fireEvent.click(del)
    await waitFor(() => expect(api.hubDelete).toHaveBeenCalledWith('/api/assets/backup/bk_1'))
  })
})

describe('NetworkTransfer', () => {
  it('载入当前配置走 export；导入前必须勾确认且 JSON 合法', async () => {
    api.hubGet.mockResolvedValue({ config_json: '{"llm":{"enabled":true}}' })
    api.hubPost.mockResolvedValue({ ok: true })
    render(<NetworkTransfer />)

    fireEvent.click(screen.getByRole('button', { name: '载入当前配置' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/network-config/export'))
    await waitFor(() => expect((screen.getByLabelText('网络配置 JSON') as HTMLTextAreaElement).value).toContain('"llm"'))

    const importBtn = screen.getByRole('button', { name: '导入网络配置' }) as HTMLButtonElement
    expect(importBtn.disabled, '导入会整体替换配置，必须先确认').toBe(true)
    fireEvent.click(screen.getByLabelText('确认导入配置'))
    fireEvent.click(importBtn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/network-config/import', {
      config: { llm: { enabled: true } },
    }))
  })

  it('坏 JSON 不发请求', async () => {
    render(<NetworkTransfer />)
    fireEvent.change(screen.getByLabelText('网络配置 JSON'), { target: { value: '{oops' } })
    fireEvent.click(screen.getByLabelText('确认导入配置'))
    fireEvent.click(screen.getByRole('button', { name: '导入网络配置' }))
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })
})
