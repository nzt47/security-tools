/**
 * 本地资源 —— 14 条端点（文件 / 工作区 / 进程）
 * ------------------------------------------------------------------
 * 这页权限最重，测试重点不在"渲染好看"，而在三条边界：
 *   1. 列目录带 path 参数，且绝对路径要上屏（不让人对着相对路径猜）；
 *   2. 写文件/写工作区/删工作区/终止进程都必须先勾确认；
 *   3. 进程只能从白名单里选，终止必须给 pid。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import LocalResources from './index'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/filesystem/list')) {
      return { abs_path: 'C:\\Users\\Administrator\\agent', items: [{ name: 'app_server.py', type: 'file', size: 100 }, { name: 'agent', type: 'dir', size: 0 }] }
    }
    if (url === '/api/workspace') return { items: [{ name: 'notes.md', type: 'file', size: 12 }] }
    if (url === '/api/workspace/info') return { path: 'C:\\...\\workspace', file_count: 3, total_size_bytes: 100 }
    if (url === '/api/process/whitelist') return { all: ['notepad.exe', 'git.exe'], custom: ['git.exe'], default: ['notepad.exe'] }
    if (url.startsWith('/api/filesystem/info')) return { ok: true, abs_path: 'C:\\x\\agent', type: 'dir' }
    if (url.startsWith('/api/filesystem/search')) return { matches: [] }
    return {}
  })
}

describe('LocalResources', () => {
  it('列目录带 path，绝对路径上屏', async () => {
    gets()
    render(<LocalResources />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/filesystem/list?path=.'))
    expect(await screen.findByText('app_server.py')).toBeTruthy()
    expect(screen.getByText('C:\\Users\\Administrator\\agent')).toBeTruthy()
  })

  it('点目录「进入」会带新路径重新列目录', async () => {
    gets()
    render(<LocalResources />)
    await screen.findByText('agent')
    fireEvent.click(screen.getByRole('button', { name: '进入 agent' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/filesystem/list?path=' + encodeURIComponent('./agent')))
  })

  it('写文件：未勾确认时禁用；确认后 POST {path, content}', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<LocalResources />)
    await screen.findByText('app_server.py')

    const write = screen.getByRole('button', { name: '写入文件' }) as HTMLButtonElement
    fireEvent.change(screen.getByLabelText('写入文件路径'), { target: { value: 'C:\\tmp\\a.txt' } })
    fireEvent.change(screen.getByLabelText('写入文件内容'), { target: { value: 'hello' } })
    expect(write.disabled, '覆盖写入是不可撤销动作，必须先勾确认').toBe(true)
    fireEvent.click(screen.getByLabelText('确认写入文件'))
    expect(write.disabled).toBe(false)
    fireEvent.click(write)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/filesystem/write', {
      path: 'C:\\tmp\\a.txt', content: 'hello',
    }))
  })

  it('工作区 Tab：写入与删除各自需勾确认', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<LocalResources />)
    await screen.findByText('app_server.py')
    fireEvent.click(screen.getByRole('button', { name: '工作区' }))

    fireEvent.change(screen.getByLabelText('工作区路径'), { target: { value: 'notes/todo.md' } })
    const write = screen.getByRole('button', { name: '写入工作区' }) as HTMLButtonElement
    const del = screen.getByRole('button', { name: '删除工作区文件' }) as HTMLButtonElement
    expect(write.disabled).toBe(true)
    expect(del.disabled).toBe(true)

    fireEvent.click(screen.getByLabelText('确认删除工作区文件'))
    fireEvent.click(del)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/workspace/delete', { path: 'notes/todo.md' }))
  })

  it('进程 Tab：只能从白名单选程序；终止需 pid + 确认', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, pid: 4321 })
    render(<LocalResources />)
    await screen.findByText('app_server.py')
    fireEvent.click(screen.getByRole('button', { name: '进程管控' }))

    const start = screen.getByRole('button', { name: '启动进程' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('选择程序'), { target: { value: 'git.exe' } })
    fireEvent.click(start)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/process/start', { program: 'git.exe' }))

    const stop = screen.getByRole('button', { name: '终止进程' }) as HTMLButtonElement
    expect(stop.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('进程 pid'), { target: { value: '4321' } })
    fireEvent.click(screen.getByLabelText('确认终止进程'))
    fireEvent.click(stop)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/process/stop', { pid: 4321 }))
  })

  it('白名单：加入与移除自定义条目', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<LocalResources />)
    await screen.findByText('app_server.py')
    fireEvent.click(screen.getByRole('button', { name: '进程管控' }))

    fireEvent.change(screen.getByLabelText('新增白名单程序'), { target: { value: 'code.exe' } })
    fireEvent.click(screen.getByRole('button', { name: '添加白名单' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/process/whitelist/add', { program: 'code.exe' }))

    fireEvent.click(screen.getByRole('button', { name: '移除白名单 git.exe' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/process/whitelist/remove', { program: 'git.exe' }))
  })
})
