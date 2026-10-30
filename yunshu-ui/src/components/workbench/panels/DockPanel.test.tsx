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

function installFetch(opts: { sandbox?: unknown } = {}) {
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
    if (url.includes('/api/sandbox/run')) {
      return res(opts.sandbox ?? { ok: true, stdout: 'hello\n', duration_ms: 2.5 })
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

  it('终端 Tab：如实显示沙盒 stdout', async () => {
    installFetch()
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /终端/ }))
    fireEvent.click(screen.getByRole('button', { name: /运行/ }))
    await waitFor(() => expect(screen.getByText(/hello/)).toBeInTheDocument())
  })

  it('终端 Tab：沙盒未启用时显示后端原因，而不是空输出', async () => {
    installFetch({
      sandbox: { ok: false, sandbox_disabled: true, error: '沙盒功能已关闭，设置环境变量 YUNSHU_FEATURE_SANDBOX=true 可启用' },
    })
    render(<DockPanel />)
    fireEvent.click(screen.getByRole('tab', { name: /终端/ }))
    fireEvent.click(screen.getByRole('button', { name: /运行/ }))
    await waitFor(() => expect(screen.getByText(/沙盒未启用/)).toBeInTheDocument())
    expect(screen.getByText(/YUNSHU_FEATURE_SANDBOX/)).toBeInTheDocument()
  })
})
