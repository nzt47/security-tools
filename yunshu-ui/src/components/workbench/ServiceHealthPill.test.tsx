/**
 * ServiceHealthPill 行为测试（2026-11-28）
 * ------------------------------------------------------------------
 * 钉住：健康静默 / 不可达出现可重试药丸 / 重试成功转绿并自动消失 / 离线优先提示。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import ServiceHealthPill from './ServiceHealthPill'

const fetchMock = vi.fn()

function okResponse() {
  const raw = '[]'
  return { ok: true, status: 200, text: async () => raw, json: async () => [] }
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('服务连接指示', () => {
  it('健康时完全静默（不占位）', async () => {
    fetchMock.mockResolvedValue(okResponse())
    render(<ServiceHealthPill pollMs={60_000} />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByText('服务不可达 · 重试')).toBeNull()
    expect(screen.queryByText('网络已断开')).toBeNull()
  })

  it('探测失败 ⇒ 出现「服务不可达 · 重试」', async () => {
    fetchMock.mockRejectedValue(new TypeError('network down'))
    render(<ServiceHealthPill pollMs={60_000} />)
    expect(await screen.findByText('服务不可达 · 重试')).toBeTruthy()
  })

  it('点击重试：成功转绿「连接已恢复」，驻留后消失', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('network down'))
    fetchMock.mockResolvedValue(okResponse())
    render(<ServiceHealthPill pollMs={60_000} recoveryMs={30} minCheckingMs={0} />)

    fireEvent.click(await screen.findByText('服务不可达 · 重试'))
    expect(await screen.findByText('连接已恢复')).toBeTruthy()
    await waitFor(() => expect(screen.queryByText('连接已恢复')).toBeNull())
  })

  it('浏览器离线 ⇒ 优先显示「网络已断开」', async () => {
    fetchMock.mockResolvedValue(okResponse())
    render(<ServiceHealthPill pollMs={60_000} />)
    act(() => { window.dispatchEvent(new Event('offline')) })
    expect(await screen.findByText('网络已断开')).toBeTruthy()
  })
})
