/**
 * ConfigChangeLogPanel 测试（§7 分批接入第一条）
 * 成功渲染 / ok=false 上屏错误 / 空库空态 / 请求异常。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

const requestMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/apiClient', () => ({ request: requestMock }))

import ConfigChangeLogPanel from './ConfigChangeLogPanel'

afterEach(() => {
  cleanup()
  requestMock.mockReset()
})

describe('ConfigChangeLogPanel', () => {
  it('成功：渲染配置变更行，且请求带 limit', async () => {
    requestMock.mockResolvedValue({
      ok: true,
      logs: [{ id: '1', timestamp: '2026-10-11T10:00:00', action: 'update', section: 'mcp_service', details: { id: 'm1' } }],
    })
    render(<ConfigChangeLogPanel />)
    expect(await screen.findByTestId('config-change-log-table')).toBeTruthy()
    expect(screen.getByText('修改')).toBeTruthy()
    expect(screen.getByText('mcp_service')).toBeTruthy()
    expect(requestMock).toHaveBeenCalledWith('/api/config/logs?limit=20')
  })

  it('ok=false：上屏错误，不渲染表格', async () => {
    requestMock.mockResolvedValue({ ok: false, error: 'boom' })
    render(<ConfigChangeLogPanel />)
    const err = await screen.findByTestId('config-change-log-error')
    expect(err.textContent).toContain('boom')
    expect(screen.queryByTestId('config-change-log-table')).toBeNull()
  })

  it('空库：显示空态而非表格', async () => {
    requestMock.mockResolvedValue({ ok: true, logs: [] })
    render(<ConfigChangeLogPanel />)
    expect(await screen.findByTestId('config-change-log-empty')).toBeTruthy()
  })

  it('请求异常：上屏错误', async () => {
    requestMock.mockRejectedValue(new Error('HTTP 500'))
    render(<ConfigChangeLogPanel />)
    await waitFor(() =>
      expect(screen.getByTestId('config-change-log-error').textContent).toContain('HTTP 500'))
  })
})
