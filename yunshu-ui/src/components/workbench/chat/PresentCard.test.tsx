/**
 * PresentCard 测试（E1-3a，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住：列出文件与数量；复制路径走剪贴板；"显示"调既有 workspace reveal（不读文件）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PresentCard } from './PresentCard'
import { useLayoutStore } from '../../../stores/useLayoutStore'
import { sessionApi } from '../../../lib/sessionApi'

vi.mock('../../../lib/sessionApi', () => ({
  sessionApi: { revealWorkspace: vi.fn().mockResolvedValue({ ok: true }) },
}))

const FILES = [
  { path: 'docs/plan/a.md', title: '设计页' },
  { path: 'src/b.py' },
]

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  useLayoutStore.setState({ activeSessionId: 's1' })
})

describe('PresentCard · 交付文件卡', () => {
  it('列出文件、标题与数量', () => {
    render(<PresentCard files={FILES} note="本次交付" />)
    expect(screen.getByTestId('present-card')).toBeTruthy()
    expect(screen.getByText('设计页')).toBeTruthy()
    expect(screen.getByText('src/b.py')).toBeTruthy()
    expect(screen.getByText('2')).toBeTruthy()
    expect(screen.getByText('本次交付')).toBeTruthy()
  })

  it('复制路径写入剪贴板', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<PresentCard files={FILES} />)
    fireEvent.click(screen.getByTestId('present-copy-docs/plan/a.md'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('docs/plan/a.md'))
  })

  it('显示 ⇒ 调 workspace reveal（会话级目录，不读任意文件）', async () => {
    render(<PresentCard files={FILES} />)
    fireEvent.click(screen.getByTestId('present-reveal-src/b.py'))
    await waitFor(() => expect(vi.mocked(sessionApi.revealWorkspace)).toHaveBeenCalledWith('s1'))
  })

  it('reveal 失败显示错误', async () => {
    vi.mocked(sessionApi.revealWorkspace).mockRejectedValueOnce(new Error('工作区不存在'))
    render(<PresentCard files={FILES} />)
    fireEvent.click(screen.getByTestId('present-reveal-src/b.py'))
    await waitFor(() => expect(screen.getByText('工作区不存在')).toBeTruthy())
  })
})
