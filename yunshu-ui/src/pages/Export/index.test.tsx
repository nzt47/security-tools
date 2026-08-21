/**
 * DataExport 导出页测试（含 mock 数据）
 * ------------------------------------------------------
 * 覆盖：
 *   1. csvCell / buildCsv 纯函数：表头、转义规则、状态中文映射
 *   2. 组件：拉取数据后渲染统计与表格
 *   3. 导出 CSV / JSON：downloadFile 收到正确的文件名、内容与 MIME
 *   4. 异常：接口失败时展示错误信息、导出按钮禁用
 * mock 数据与 src/mocks/devMock.ts 对齐（26 条用户，含 admin）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { getUserList } from '@/api/user'
import { downloadFile } from '@/utils/system'
import DataExport, { buildCsv, csvCell } from './index'

// ---- mock 数据：26 条用户（结构对齐 devMock.ts 的 MockListUser） ----
const MOCK_USERS = Array.from({ length: 26 }, (_, i) => {
  const id = i + 1
  return {
    id,
    username: id === 1 ? 'admin' : `user${String(id).padStart(2, '0')}`,
    email: `user${id}@yunshu.local`,
    role: id === 1 ? 'admin' : id % 3 === 0 ? 'manager' : 'user',
    status: (id % 5 === 0 ? 0 : 1) as 0 | 1,
    createdAt: `2026-0${(id % 9) + 1}-${String((id % 27) + 1).padStart(2, '0')} 10:30:00`,
  }
})

// vi.mock 工厂会被提升到 import 之前，mock 函数需用 vi.hoisted 定义
const { mockGetUserList, mockDownloadFile } = vi.hoisted(() => ({
  mockGetUserList: vi.fn(),
  mockDownloadFile: vi.fn(),
}))

vi.mock('@/api/user', () => ({
  getUserList: mockGetUserList,
  getUserInfo: vi.fn(),
  getExportMockUsers: vi.fn(),
}))

vi.mock('@/utils/system', () => ({
  downloadFile: mockDownloadFile,
}))

beforeEach(() => {
  mockGetUserList.mockReset()
  mockDownloadFile.mockReset()
  mockGetUserList.mockResolvedValue({ list: MOCK_USERS, total: MOCK_USERS.length })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('csvCell / buildCsv 纯函数', () => {
  it('csvCell：逗号/引号/换行需转义，普通值与数字原样返回', () => {
    expect(csvCell('plain')).toBe('plain')
    expect(csvCell('a,b')).toBe('"a,b"')
    expect(csvCell('say "hi"')).toBe('"say ""hi"""')
    expect(csvCell('line\nbreak')).toBe('"line\nbreak"')
    expect(csvCell(42)).toBe('42')
  })

  it('buildCsv：表头 + 行内容 + 状态中文映射', () => {
    const csv = buildCsv([MOCK_USERS[0]])
    const lines = csv.split('\n')
    expect(lines[0]).toBe('ID,用户名,邮箱,角色,状态,创建时间')
    expect(lines[1]).toContain('1,admin')
    expect(lines[1]).toContain('启用')
  })
})

describe('DataExport 组件', () => {
  it('拉取数据并渲染统计与表格', async () => {
    render(<DataExport />)

    // 统计：26 条中 5 条禁用（id 为 5 的倍数），1 条 admin
    expect(await screen.findByText(/共 26 条 · 启用 21 · 管理员 1/)).toBeInTheDocument()
    // admin 同时出现在「用户名」列与「角色」徽标，故用 getAllByText 断言存在
    expect(screen.getAllByText('admin').length).toBeGreaterThan(0)
    expect(screen.getByText('user26')).toBeInTheDocument()
    expect(mockGetUserList).toHaveBeenCalledWith({ page: 1, pageSize: 100 })
  })

  it('导出 CSV：文件名 / MIME / 内容正确', async () => {
    render(<DataExport />)
    await screen.findByText(/共 26 条/)

    fireEvent.click(screen.getByRole('button', { name: /导出/ }))
    await waitFor(() => expect(mockDownloadFile).toHaveBeenCalledTimes(1))

    const [name, content, mime] = mockDownloadFile.mock.calls[0]
    // 时间戳文件名：yunshu-users-2026-08-19T10-55-27.csv
    expect(name).toMatch(/^yunshu-users-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.csv$/)
    expect(mime).toBe('text/csv;charset=utf-8')
    // 分片数组内容由 Blob 内部拼接（无中间大字符串），语义与整串一致
    const csv = (content as string[]).join('')
    expect(csv).toContain('ID,用户名,邮箱,角色,状态,创建时间')
    expect(csv).toContain('user26')
  })

  it('切换 JSON 后导出：内容可解析且条数一致', async () => {
    render(<DataExport />)
    await screen.findByText(/共 26 条/)

    fireEvent.click(screen.getByRole('radio', { name: /JSON/ }))
    fireEvent.click(screen.getByRole('button', { name: /导出/ }))
    await waitFor(() => expect(mockDownloadFile).toHaveBeenCalledTimes(1))

    const [name, content, mime] = mockDownloadFile.mock.calls[0]
    expect(name).toMatch(/\.json$/)
    expect(mime).toBe('application/json')
    const parsed = JSON.parse((content as string[]).join(''))
    expect(parsed).toHaveLength(26)
    expect(parsed[0].username).toBe('admin')
  })

  it(
    '大数据量（5000 条）分片导出：CSV 带 BOM 前缀且行数完整',
    { timeout: 20000 },
    async () => {
      const bigUsers = Array.from({ length: 5000 }, (_, i) => ({
        id: i + 1,
        username: `user${i + 1}`,
        email: `user${i + 1}@yunshu.local`,
        role: 'user',
        status: 1 as const,
        createdAt: '2026-08-01 10:30:00',
      }))
      mockGetUserList.mockResolvedValue({ list: bigUsers, total: bigUsers.length })
      render(<DataExport />)
      await screen.findByText(/共 5000 条/)

      fireEvent.click(screen.getByRole('button', { name: /导出/ }))
      // 分片异步执行：等 downloadFile 最终被调用（内部经过 3 个 2000 条分片）
      await waitFor(() => expect(mockDownloadFile).toHaveBeenCalledTimes(1))

      const [name, content, mime] = mockDownloadFile.mock.calls[0]
      expect(name).toMatch(/\.csv$/)
      expect(mime).toBe('text/csv;charset=utf-8')
      // CSV 内容最前是 BOM，随后表头；行数 = 5000 数据行 + 1 表头行
      const csv = (content as string[]).join('')
      expect(csv).toMatch(/^\ufeffID,用户名,邮箱,角色,状态,创建时间/)
      expect(csv.split('\n')).toHaveLength(5001)
    },
    // 【Why 20000】5000 行 × 3 分片 + yieldToMain 实际耗时约 3s，全量共享环境会放大超过默认 5000ms 阈值
  )

  it('数据拉取失败：展示错误信息，导出按钮禁用', async () => {
    mockGetUserList.mockRejectedValue(new Error('网络异常'))
    render(<DataExport />)

    expect(await screen.findByText('网络异常')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出/ })).toBeDisabled()
  })

  it(
    '导出中途点击取消：中断分片、不触发下载、状态清理',
    { timeout: 20000 },
    async () => {
      const bigUsers = Array.from({ length: 5000 }, (_, i) => ({
        id: i + 1,
        username: `user${i + 1}`,
        email: `user${i + 1}@yunshu.local`,
        role: 'user',
        status: 1 as const,
        createdAt: '2026-08-01 10:30:00',
      }))
      mockGetUserList.mockResolvedValue({ list: bigUsers, total: bigUsers.length })
      render(<DataExport />)
      await screen.findByText(/共 5000 条/)

      // 点击导出后，等「取消」按钮出现（exporting=true，确认 handleExport 已开始挂起），再点击取消，
      // 避免与 handleExport 开头 `cancelRef.current = false` 的同步段产生时序竞态
      fireEvent.click(screen.getByRole('button', { name: /导出/ }))
      const cancelBtn = await screen.findByRole('button', { name: '取消' })
      fireEvent.click(cancelBtn)

      // 分片任务被中断：downloadFile 不应被调用，状态恢复（导出按钮重新可用、取消按钮消失）
      await waitFor(() => expect(screen.getByRole('button', { name: /导出/ })).toBeEnabled())
      expect(mockDownloadFile).not.toHaveBeenCalled()
      expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument()
    },
    // 【Why 20000】大数据量分片 + 取消中断，全量共享环境耗时放大，需提高阈值（同上方 5000 条用例）
  )
})
