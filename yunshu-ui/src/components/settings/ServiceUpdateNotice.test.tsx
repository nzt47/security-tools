/**
 * ServiceUpdateNotice 行为测试（2026-11-28）
 * ------------------------------------------------------------------
 * 钉住：只有「页面 bundle ≠ 服务端 bundle」才提示；同版不打扰；「稍后」后同一版本不再弹。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import ServiceUpdateNotice from './ServiceUpdateNotice'

const fetchMock = vi.fn()

// apiClient.request 读的是 res.text()（不是 res.json），mock 必须同时给出 text
function jsonResponse(body: unknown) {
  const raw = JSON.stringify(body)
  return { ok: true, status: 200, text: async () => raw, json: async () => body }
}

function payload(serverBundle: string) {
  return {
    code: 200,
    data: {
      backend: { commit: 'ff133a287' },
      frontend: { bundle: serverBundle },
      process: { pid: 1 },
    },
  }
}

let script: HTMLScriptElement | null = null

function setPageBundle(name: string) {
  script = document.createElement('script')
  script.setAttribute('src', '/static/assets/' + name)
  document.head.appendChild(script)
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  script?.remove()
  script = null
})

describe('服务已更新提示', () => {
  it('页面 bundle 落后于服务端 ⇒ 弹提示，展示两侧 bundle', async () => {
    setPageBundle('index-OLD.js')
    fetchMock.mockImplementation(async () => jsonResponse(payload('index-NEW.js')))

    render(<ServiceUpdateNotice />)
    expect(await screen.findByText('服务已更新')).toBeTruthy()
    expect(screen.getByText('index-OLD.js')).toBeTruthy()
    expect(screen.getByText('index-NEW.js')).toBeTruthy()
  })

  it('同版 ⇒ 不打扰', async () => {
    setPageBundle('index-SAME.js')
    fetchMock.mockImplementation(async () => jsonResponse(payload('index-SAME.js')))

    render(<ServiceUpdateNotice />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByText('服务已更新')).toBeNull()
  })

  it('点「稍后」关闭；同一服务端版本不再弹', async () => {
    setPageBundle('index-OLD.js')
    fetchMock.mockImplementation(async () => jsonResponse(payload('index-NEW.js')))

    render(<ServiceUpdateNotice />)
    fireEvent.click(await screen.findByRole('button', { name: '稍后' }))
    await waitFor(() => expect(screen.queryByText('服务已更新')).toBeNull())
  })

  it('开发模式（没有 index-*.js）不请求版本接口', async () => {
    render(<ServiceUpdateNotice />)
    await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
