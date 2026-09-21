/**
 * BackgroundTasksMenu 测试 —— 「后台任务下拉」的**运行时数据链路**
 * ------------------------------------------------------------------
 * 需求④「会话任务（后台任务下拉）」的覆盖空洞：本组件此前**零测试**。
 * 本文件不写"静态字符串断言"，而是驱动真实交互链：
 *   挂载/展开 → GET /api/background/tasks → 用**接口返回的运行时数据**渲染
 *   → 展开某行看详情 → POST /api/background/tasks/<id>/cancel → 重新拉取
 *   → 打开态 5s 轮询 / 关闭态停轮询。
 * 若哪天把下拉改成"只渲染写死的假数据"，下面所有断言都会失败（值取自桩的运行时数据）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { BackgroundTasksMenu } from './BackgroundTasksMenu'

interface Stub {
  status?: number
  body: unknown
}

/** 按 URL/方法分派的 fetch 桩（记录调用，供断言"打了哪个端点"） */
function installFetch(handler: (url: string, method: string, call: number) => Stub) {
  const calls: { url: string; method: string }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method })
    const r = handler(url, method, calls.length)
    const status = r.status ?? 200
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => r.body,
    } as unknown as Response
  })
  vi.stubGlobal('fetch', fn)
  return calls
}

/** 运行时任务数据（真值来自接口；测试断言渲染出的就是这些值） */
const TASKS = {
  ok: true,
  total: 2,
  active: 1,
  tasks: [
    {
      id: 'task-abc-001', name: '过程蒸馏', tool_name: 'process_distill_run', status: 'running',
      progress: '3/10 文档', created_at: '2026-09-21T10:00:00', started_at: '2026-09-21T10:00:05',
      completed_at: null, timeout: 600, has_result: false,
    },
    {
      id: 'task-def-002', name: '批量导出', tool_name: 'bulk_export', status: 'completed',
      progress: '', created_at: '2026-09-21T09:00:00', started_at: '2026-09-21T09:00:01',
      completed_at: '2026-09-21T09:02:00', timeout: null, has_result: true,
    },
  ],
}

const LIST_OK: Stub = { body: TASKS }

function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: /后台任务/ }))
}

describe('BackgroundTasksMenu · 运行时数据链路', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('未展开也先探一次运行中数量（角标取自接口 active，而非写死）', async () => {
    const calls = installFetch(() => LIST_OK)
    render(<BackgroundTasksMenu />)
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    expect(calls[0].url).toContain('/api/background/tasks')
    // 角标 = 接口返回的 active(1)
    const btn = screen.getByRole('button', { name: /后台任务/ })
    await waitFor(() => expect(btn.textContent).toContain('1'))
  })

  it('展开即拉取列表，并用接口返回的字段渲染（id / 工具名 / 进度 / 状态标签）', async () => {
    const calls = installFetch(() => LIST_OK)
    render(<BackgroundTasksMenu />)
    openMenu()

    await waitFor(() => expect(screen.getByText('过程蒸馏')).toBeInTheDocument())
    // 行内副标题：tool_name · id · progress —— 全部来自运行时数据
    expect(screen.getByText(/process_distill_run · task-abc-001 · 3\/10 文档/)).toBeInTheDocument()
    expect(screen.getByText('批量导出')).toBeInTheDocument()
    expect(screen.getByText(/bulk_export · task-def-002/)).toBeInTheDocument()
    // 状态标签映射（running→运行中 / completed→已完成）
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    // 头部统计取自 total/active
    expect(screen.getByText(/共 2 · 运行中 1/)).toBeInTheDocument()
    expect(calls.some((c) => c.url.includes('limit=50'))).toBe(true)
  })

  it('换一份运行时数据 ⇒ 渲染随之改变（证明不是静态文案）', async () => {
    installFetch(() => ({
      body: { ok: true, total: 1, active: 0, tasks: [
        { id: 'only-1', tool_name: 'sleepy_job', status: 'failed', error: '上游超时' },
      ] },
    }))
    render(<BackgroundTasksMenu />)
    openMenu()
    await waitFor(() => expect(screen.getByText('sleepy_job')).toBeInTheDocument())
    expect(screen.queryByText('过程蒸馏')).not.toBeInTheDocument()
    expect(screen.getByText('失败')).toBeInTheDocument()
  })

  it('展开某行显示详情（创建/开始/完成/超时/结果标记），数据来自接口', async () => {
    installFetch(() => LIST_OK)
    render(<BackgroundTasksMenu />)
    openMenu()
    await waitFor(() => expect(screen.getByText('批量导出')).toBeInTheDocument())

    // 第二行 = 已完成那个（列表顺序取自接口返回顺序）
    fireEvent.click(screen.getAllByTitle('展开/收起任务详情')[1])
    await waitFor(() => expect(screen.getByText(/创建：09-21 09:00:00/)).toBeInTheDocument())
    expect(screen.getByText(/完成：09-21 09:02:00/)).toBeInTheDocument()
    expect(screen.getByText(/已有结果/)).toBeInTheDocument()
    expect(screen.queryByText(/超时：/)).not.toBeInTheDocument()
  })

  it('取消运行中任务：POST 到该 id 的 cancel 端点，并重新拉取列表', async () => {
    let listCalls = 0
    const calls = installFetch((url, method) => {
      if (method === 'POST' && url.includes('/cancel')) return { body: { ok: true } }
      listCalls += 1
      return LIST_OK
    })
    render(<BackgroundTasksMenu />)
    openMenu()
    await waitFor(() => expect(screen.getByText('过程蒸馏')).toBeInTheDocument())
    const before = listCalls

    fireEvent.click(screen.getByRole('button', { name: /取消/ }))
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url === '/api/background/tasks/task-abc-001/cancel')).toBe(true),
    )
    // 取消后必须重新拉取（否则界面停在旧状态）
    await waitFor(() => expect(listCalls).toBeGreaterThan(before))
  })

  it('已完成任务不显示取消按钮（只有 pending/running 可取消）', async () => {
    installFetch(() => LIST_OK)
    render(<BackgroundTasksMenu />)
    openMenu()
    await waitFor(() => expect(screen.getByText('批量导出')).toBeInTheDocument())
    // 两个任务里只有 running 那个有取消按钮
    expect(screen.getAllByRole('button', { name: /取消/ })).toHaveLength(1)
  })

  it('接口报错时显式提示（不静默吞掉）', async () => {
    installFetch(() => ({ body: { ok: false, error: '后台执行器未启用' } }))
    render(<BackgroundTasksMenu />)
    openMenu()
    await waitFor(() => expect(screen.getByText(/后台执行器未启用/)).toBeInTheDocument())
  })

  it('展开态每 5s 轮询；关闭后停止轮询（不残留后台请求）', async () => {
    vi.useFakeTimers()
    const calls = installFetch(() => LIST_OK)
    render(<BackgroundTasksMenu />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const afterMount = calls.length

    fireEvent.click(screen.getByRole('button', { name: /后台任务/ }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const afterOpen = calls.length
    expect(afterOpen).toBeGreaterThan(afterMount)

    // 5s 后应再来一次
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(calls.length).toBeGreaterThan(afterOpen)

    // 关闭后：轮询必须停（再等 15s 不再新增请求）
    fireEvent.click(screen.getByRole('button', { name: '关闭后台任务菜单' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const afterClose = calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    expect(calls.length).toBe(afterClose)
  })
})
