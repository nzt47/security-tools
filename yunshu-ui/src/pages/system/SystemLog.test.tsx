/**
 * SystemLog 组件单元测试
 * ------------------------------------------------
 * 覆盖：
 *   - 表格渲染（6 条 mock 日志）
 *   - 导出按钮权限显隐（admin 可见 / user 无 system:log:export 隐藏）
 *   - 点击导出触发 CSV 下载（文件名规范 + Blob 生成 + anchor click）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useUserStore } from '@/store/userStore'
import type { UserInfo } from '@/api/user'
import SystemLog from './SystemLog'

function setUser(overrides: Partial<UserInfo> & { role?: string }) {
  useUserStore.setState({
    userInfo: {
      id: 1,
      username: 'tester',
      nickname: '测试用户',
      ...overrides,
    } as UserInfo,
  })
}

/** 导出链路 mock：Blob URL + anchor click 捕获（避免真实下载） */
let capturedDownload: string | null = null
let capturedBlob: Blob | null = null
const createObjectURL = vi.fn((blob: Blob) => {
  capturedBlob = blob
  return 'blob:mock-csv'
})
const revokeObjectURL = vi.fn()
const clickSpy = vi
  .spyOn(HTMLAnchorElement.prototype, 'click')
  .mockImplementation(function (this: HTMLAnchorElement) {
    capturedDownload = this.download
  })

describe('SystemLog 系统日志', () => {
  beforeEach(() => {
    capturedDownload = null
    capturedBlob = null
    createObjectURL.mockClear()
    revokeObjectURL.mockClear()
    clickSpy.mockClear()
    useUserStore.setState({ userInfo: null })
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('渲染 6 条 mock 日志表格', () => {
    setUser({ role: 'admin' })
    render(<SystemLog />)
    expect(screen.getByText('系统日志')).toBeInTheDocument()
    // 表头 1 行 + 数据 6 行
    expect(screen.getAllByRole('row')).toHaveLength(7)
    expect(screen.getByText('删除用户 user12')).toBeInTheDocument()
  })

  it('admin 角色可见导出按钮', () => {
    setUser({ role: 'admin' })
    render(<SystemLog />)
    expect(screen.getByRole('button', { name: /导出日志/ })).toBeInTheDocument()
  })

  it('user 无 system:log:export 权限时不渲染导出按钮', () => {
    setUser({ role: 'user', permissions: ['system:view'] })
    render(<SystemLog />)
    expect(screen.queryByRole('button', { name: /导出日志/ })).not.toBeInTheDocument()
  })

  it('user 命中 system:log:export 权限码时可见导出按钮', () => {
    setUser({ role: 'user', permissions: ['system:view', 'system:log:export'] })
    render(<SystemLog />)
    expect(screen.getByRole('button', { name: /导出日志/ })).toBeInTheDocument()
  })

  it('点击导出：生成 CSV Blob（含 BOM + 表头 + 数据）并触发下载', async () => {
    setUser({ role: 'admin' })
    render(<SystemLog />)
    fireEvent.click(screen.getByRole('button', { name: /导出日志/ }))

    // 触发 anchor click 且文件名符合规范 system-log-YYYY-MM-DD.csv
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(capturedDownload).toMatch(/^system-log-\d{4}-\d{2}-\d{2}\.csv$/)
    // Blob 内容：BOM + 表头 + 6 行（jsdom Blob 无 text()，用 FileReader 读取）
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(capturedBlob).toBeInstanceOf(Blob)
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = () => reject(reader.error)
      reader.readAsText(capturedBlob as Blob)
    })
    // jsdom 解码 UTF-8 会剥离 BOM（EF BB BF），校验表头与数据行数即可
    expect(content.startsWith('时间,操作人,操作,结果\n')).toBe(true)
    expect(content.split('\n')).toHaveLength(7) // 表头 + 6 数据行（无尾部换行）
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-csv')
  })
})
