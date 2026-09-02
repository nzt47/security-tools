/**
 * Toaster 挂载点回归测试 —— 缺陷 ①
 * ------------------------------------------------
 * 背景：toast.success/error 由 utils/request.ts 拦截器与 Login、pages/system 页面
 * 生产调用，但 <Toaster/> 容器此前从未在生产挂载（main.tsx 只渲染 AppRouter），
 * 用户看不到任何提示。
 * 修复：在登录页宿主 LoginLayout 与工作台根 WorkbenchApp 各挂载 <Toaster/>（单例
 * 幂等）。本测试挂载两个宿主并触发 toast，断言提示文案真实渲染到 DOM。
 *
 * 注意：Toaster 为模块级单例（items 驻留 3s 后自动清除），同文件用例间用
 * 假时钟推进清除遗留 toast，避免跨用例残留导致计数断言失真。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { toast } from '@/components/Toaster'
import LoginLayout from '@/layouts/LoginLayout'
import WorkbenchApp from '@/WorkbenchApp'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  // 让遗留 toast 走完 3s 展示期（自动清除），再切回真实时钟，保证模块级 items 干净
  act(() => {
    vi.advanceTimersByTime(5000)
  })
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Toaster 挂载点（缺陷①）', () => {
  it('登录页宿主 LoginLayout 挂载 <Toaster/>：登录失败/校验提示可见', () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginLayout />} />
        </Routes>
      </MemoryRouter>,
    )

    act(() => {
      toast.error('请输入用户名和密码')
    })
    // toast 文案真实渲染为 role=alert 提示
    expect(screen.getByRole('alert')).toHaveTextContent('请输入用户名和密码')
  })

  it('工作台根 WorkbenchApp 挂载 <Toaster/>：Hub 页面（如系统管理）操作提示可见', () => {
    // WorkbenchChatPage 挂载时会请求 /api/sessions（真实 fetch 在 jsdom 不可用），
    // stub 为返回空会话，避免真实网络请求
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ sessions: [], current_id: null }) })),
    )

    render(<WorkbenchApp />)

    act(() => {
      toast.success('用户已更新')
    })
    expect(screen.getByRole('alert')).toHaveTextContent('用户已更新')
  })

  it('同一提示防重复：单例下重复 push 相同文案只渲染一条', () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginLayout />} />
        </Routes>
      </MemoryRouter>,
    )

    act(() => {
      toast.error('幂等校验提示')
      toast.error('幂等校验提示')
      toast.error('幂等校验提示')
    })
    // Toaster 内部防重复入列：3 次同文案 push 只渲染 1 条
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('alert')).toHaveTextContent('幂等校验提示')
  })
})
