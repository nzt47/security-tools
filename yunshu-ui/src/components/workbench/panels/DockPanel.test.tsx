/**
 * DockPanel 测试 —— 右侧可切换边栏（编辑器/文档/子代理/浏览器/终端）
 * ------------------------------------------------------------------
 * 锁四件事：
 *   1. 默认落在「编辑器」（历史布局只有 code 一格，不能一升级就把编辑器弄丢）；
 *   2. 「子代理」读**真实契约**：list 是信封 v2（data.subagents），history 是扁平 body；
 *   3. 「文档」能列目录 + 打开 .md 正文；
 *   4. 「终端」把沙盒的真实结果（stdout / 未启用原因）如实上屏。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const { DockPanel } = await import('./DockPanel')

function res(body: unknown, envelope = false) {
  const wire = envelope ? { code: 200, data: body } : body
  return {
    ok: true,
    status: 200,
    headers: { get: (n: string) => (envelope && n.toLowerCase() === 'x-envelope' ? 'v2' : null) },
    json: async () => wire,
    text: async () => JSON.stringify(wire),
  } as unknown as Response
}

function installFetch(opts: { terminal?: unknown } = {}) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/api/subagent/list')) {
      return res({ ok: true, count: 1, subagents: [{ name: 'sa-x', model_id: 'deepseek-flash' }] }, true)
    }
    if (url.includes('/api/subagent/history')) {
      return res({
        ok: true,
        records: [{ delegation_id: 'd1', subagent: 'sa-x', goal: '抽取文档要点', ok: true }],
      })
    }
    if (url.includes('/api/filesystem/list')) {
      return res({ ok: true, path: 'docs/zh', items: [{ name: 'a.md', type: 'file' }, { name: 'sub', type: 'dir' }] })
    }
    if (url.includes('/api/filesystem/read')) {
      return res({ ok: true, content: '# 文档正文' })
    }
    if (url.includes('/api/terminal/activity')) {
      return res({
        ok: true,
        rows: [
          { seq: 81503, ts: '2026-10-31T09:10:00+08:00', action: 'terminal.exec', subject: 'git-status' },
          { seq: 81504, ts: '2026-10-31T09:10:03+08:00', action: 'skill.load', subject: 'skill:handoff' },
        ],
        inflight: [{ task_id: 't1', subagent: 'sa-x', goal: '抽取文档要点' }],
        last_seq: 81504,
      })
    }
    if (url.includes('/api/terminal/commands')) {
      return res({
        ok: true,
        commands: [
          { id: 'git-status', label: 'git status', description: '工作区状态', params: [] },
          { id: 'git-log', label: 'git log', description: '最近提交', params: [{ name: 'n', type: 'int', min: 1, max: 50, default: 10 }] },
        ],
      })
    }
    if (url.includes('/api/terminal/exec')) {
      return res(opts.terminal ?? {
        ok: true, argv: ['git', 'status', '--short'], exit_code: 0,
        stdout: ' M a.ts\n', stderr: '', duration_ms: 1.2,
      })
    }
    if (url.includes('/api/browser/session/close')) return res({ ok: true, closed: true })
    if (url.includes('/api/browser/session')) {
      return res({ ok: true, session_id: 's1', width: 1280, height: 800, title: 'Example Domain' })
    }
    if (url.includes('/api/browser/frame')) {
      return {
        ok: true, status: 200,
        blob: async () => new Blob(['frame'], { type: 'image/jpeg' }),
        json: async () => ({}),
      } as unknown as Response
    }
    if (url.includes('/api/browser/input')) {
      return res({ ok: true, url: 'https://example.com/', title: 'Example Domain' })
    }
    return res({ ok: true })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('DockPanel · 右侧可切换边栏', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('默认落在编辑器（升级不丢原有能力）', () => {
    installFetch()
    render(<DockPanel />)
    expect(screen.getByRole('tab', { name: /编辑器/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByPlaceholderText(/在这里编写或粘贴代码/)).toBeInTheDocument()
  })

  it('子代理 Tab：读信封列表 + 扁平历史并上屏', async () => {
    installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /子代理/ }))
    await waitFor(() => expect(screen.getByText('sa-x')).toBeInTheDocument())
    expect(screen.getByText('成功')).toBeInTheDocument()
    expect(screen.getByText('抽取文档要点')).toBeInTheDocument()
  })

  it('文档 Tab：列目录并打开 .md 正文', async () => {
    installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /文档/ }))
    await waitFor(() => expect(screen.getByText('a.md')).toBeInTheDocument())
    fireEvent.click(screen.getByText('a.md'))
    await waitFor(() => expect(screen.getByText(/# 文档正文/)).toBeInTheDocument())
  })

  it('浏览器 Tab：拒绝 javascript: 等非 http(s) 地址（不渲染 iframe）', () => {
    installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /浏览器/ }))
    fireEvent.change(screen.getByLabelText('浏览器地址'), { target: { value: 'javascript:alert(1)' } })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    expect(screen.getByText(/只支持 http\(s\) 地址/)).toBeInTheDocument()
    expect(screen.queryByTitle('浏览器')).not.toBeInTheDocument()
  })

  it('终端 Tab：执行白名单命令并显示 argv / 退出码 / 输出', async () => {
    installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /终端/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'git status' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'git status' }))
    fireEvent.click(screen.getByRole('button', { name: /执行/ }))
    await waitFor(() => expect(screen.getByText(/exit 0/)).toBeInTheDocument())
    expect(screen.getByText(/M a\.ts/)).toBeInTheDocument()
    expect(screen.getByText(/git status --short/)).toBeInTheDocument()
  })

  it('终端 · 实时活动：显示审计链尾部的执行记录与进行中的委派', async () => {
    installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /终端/ }))
    fireEvent.click(screen.getByRole('button', { name: '实时活动' }))
    await waitFor(() => expect(screen.getByText('terminal.exec')).toBeInTheDocument())
    expect(screen.getByText('skill.load')).toBeInTheDocument()
    expect(screen.getByText(/进行中的委派/)).toBeInTheDocument()
    expect(screen.getByText(/抽取文档要点/)).toBeInTheDocument()
  })

  it('终端 Tab：命令清单读取失败时上屏错误（不是空白面板）', async () => {
    const fn = vi.fn(async () => res({ ok: false, error: '未授权' }))
    vi.stubGlobal('fetch', fn)
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /终端/ }))
    await waitFor(() => expect(screen.getByText(/未授权/)).toBeInTheDocument())
  })

  it('浏览器 · 交互模式：建会话后出帧，点击按 viewport 换算成页面坐标', async () => {
    const fn = installFetch()
    Object.defineProperty(URL, 'createObjectURL', { writable: true, configurable: true, value: vi.fn(() => 'blob:frame') })
    Object.defineProperty(URL, 'revokeObjectURL', { writable: true, configurable: true, value: vi.fn() })

    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /浏览器/ }))
    fireEvent.click(screen.getByRole('button', { name: '交互' }))
    fireEvent.change(screen.getByLabelText('浏览器地址'), { target: { value: 'https://example.com/' } })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))

    const img = await screen.findByAltText('交互浏览器画面')
    // jsdom 不做布局：给一个确定的显示尺寸，验证 scale = viewport/显示宽度
    img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 400, right: 640, bottom: 400, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect
    fireEvent.click(img, { clientX: 320, clientY: 200 })

    await waitFor(() => {
      const call = fn.mock.calls.find(([u]) => String(u).includes('/api/browser/input'))
      expect(call).toBeTruthy()
    })
    const call = fn.mock.calls.find(([u]) => String(u).includes('/api/browser/input'))
    const body = JSON.parse(String(((call?.[1] as RequestInit) || {}).body))
    expect(body).toMatchObject({ kind: 'click', x: 640, y: 400 })
    expect(screen.getByText('Example Domain')).toBeInTheDocument()
  })

  it('浏览器 · 交互模式：非 http(s) 地址仍被拒（不建会话）', async () => {
    const fn = installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /浏览器/ }))
    fireEvent.click(screen.getByRole('button', { name: '交互' }))
    fireEvent.change(screen.getByLabelText('浏览器地址'), { target: { value: 'javascript:alert(1)' } })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))

    expect(screen.getByText(/只支持 http\(s\) 地址/)).toBeInTheDocument()
    expect(fn.mock.calls.some(([u]) => String(u).includes('/api/browser/session'))).toBe(false)
  })
})
