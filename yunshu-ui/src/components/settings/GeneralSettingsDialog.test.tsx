/**
 * GeneralSettingsDialog 行为测试（2026-11-28）
 * ------------------------------------------------------------------
 * 钉住四件事：分区导航受控、偏好写入既有 store、Esc 关闭、关于页消费 /api/version。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import GeneralSettingsDialog from './GeneralSettingsDialog'
import { DEFAULT_STYLE, useChatPrefsStore } from '../../stores/useChatPrefsStore'

const fetchMock = vi.fn()

// apiClient.request 读的是 res.text()（不是 res.json），mock 必须同时给出 text
function jsonResponse(body: unknown, status = 200) {
  const raw = JSON.stringify(body)
  return { ok: status < 400, status, text: async () => raw, json: async () => body }
}

function versionPayload() {
  return {
    code: 200,
    data: {
      backend: {
        commit: 'ff133a287aca0864ad03f4a4fb3006bbc10a0da9',
        commit_subject: 'feat(e3-4): 语音输入',
        committed_at: '2026-11-28T13:10:18+08:00',
        dirty: false,
      },
      frontend: {
        bundle: 'index-CmCuhlu3.js',
        build_commit: 'ff133a287aca0864ad03f4a4fb3006bbc10a0da9',
        built_at: '2026-11-28T13:20:00+08:00',
      },
      process: { pid: 45036, python: '3.12.0', started_at: '2026-11-28T13:21:00+08:00' },
    },
  }
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async () => jsonResponse(versionPayload()))
  vi.stubGlobal('fetch', fetchMock)
  useChatPrefsStore.setState({ ...DEFAULT_STYLE, showThinking: true, showToolCalls: true })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderDialog(overrides: Partial<React.ComponentProps<typeof GeneralSettingsDialog>> = {}) {
  const onClose = vi.fn()
  const onSectionChange = vi.fn()
  render(
    <GeneralSettingsDialog
      open
      section="general"
      onSectionChange={onSectionChange}
      onClose={onClose}
      {...overrides}
    />,
  )
  return { onClose, onSectionChange }
}

describe('通用设置 · 外壳', () => {
  it('渲染四个分区，点击分区交给受控回调', () => {
    const { onSectionChange } = renderDialog()
    expect(screen.getByRole('tab', { name: '通用' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '外观' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '快捷键' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '关于' })).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: '外观' }))
    expect(onSectionChange).toHaveBeenCalledWith('appearance')
  })

  it('Esc 关闭（ModalBase 捕获阶段）', () => {
    const { onClose } = renderDialog()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('通用设置 · 偏好行', () => {
  it('显示开关写回 useChatPrefsStore', () => {
    renderDialog()
    const sw = screen.getByRole('switch', { name: '显示思考过程' })
    expect(sw.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(sw)
    expect(useChatPrefsStore.getState().showThinking).toBe(false)
    expect(useChatPrefsStore.getState().showToolCalls).toBe(true)
  })

  it('对话模式下拉写回 store（控件是受控的 Select）', () => {
    renderDialog()
    const select = screen.getByLabelText('对话模式') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'retrieval' } })
    expect(useChatPrefsStore.getState().mode).toBe('retrieval')
  })

  it('外观分区改输出格式，不动显示开关', () => {
    renderDialog({ section: 'appearance' })
    fireEvent.change(screen.getByLabelText('输出格式'), { target: { value: 'terminal' } })
    expect(useChatPrefsStore.getState().format).toBe('terminal')
    expect(useChatPrefsStore.getState().showThinking).toBe(true)
  })
})

describe('通用设置 · 关于', () => {
  it('消费 /api/version，展示短 commit 与 bundle', async () => {
    renderDialog({ section: 'about' })
    // 服务端 commit 与前端构建 commit 同值 ⇒ 两处都会出现短号
    expect((await screen.findAllByText('ff133a287')).length).toBeGreaterThan(0)
    expect(screen.getByText('index-CmCuhlu3.js')).toBeTruthy()
    await waitFor(() => {
      const called = fetchMock.mock.calls.some((call) => String(call[0]).includes('/api/version'))
      expect(called).toBe(true)
    })
  })
})
