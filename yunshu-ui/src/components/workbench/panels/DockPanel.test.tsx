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
  const fn = vi.fn(async (input: RequestInfo | URL) => {
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
    return res({ ok: true })
  })
  vi.stubGlobal('fetch', fn)
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

  it('终端 Tab：命令清单读取失败时上屏错误（不是空白面板）', async () => {
    const fn = vi.fn(async () => res({ ok: false, error: '未授权' }))
    vi.stubGlobal('fetch', fn)
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /终端/ }))
    await waitFor(() => expect(screen.getByText(/未授权/)).toBeInTheDocument())
  })
})
