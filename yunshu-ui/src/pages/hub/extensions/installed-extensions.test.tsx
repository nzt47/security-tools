/**
 * 已安装扩展（扩展系统）组件 —— 四个动作的接线与纪律
 * ------------------------------------------------------------------
 * 锁四件事：
 *   1. 只列调用方声明的 ext_type（每页只看到自己那类，不串台）；
 *   2. 启停带 {type, id, enabled}，enabled 是**目标态**（不是"切换"）；
 *   3. 卸载是两步：先展开确认条，再点「确认卸载」才发请求，且 force === true；
 *   4. 配置不是合法 JSON 时不发请求（不能把坏 JSON 当配置提交）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import InstalledExtensions from './installed-extensions'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function rows() {
  api.hubGet.mockResolvedValue({
    ok: true,
    extensions: [
      { ext_id: 'pkg-a', ext_type: 'plugin', name: '插件包 A', enabled: true, config: { k: 1 } },
      { ext_id: 'skill-a', ext_type: 'skill', name: '技能 A', enabled: false },
      { ext_id: 'mcp-a', ext_type: 'mcp', name: 'MCP A', enabled: true },
    ],
  })
}

describe('InstalledExtensions', () => {
  it('只列声明类型：types=[plugin] 时看不到 skill / mcp 行', async () => {
    rows()
    render(<InstalledExtensions types={['plugin']} title="已安装插件包" />)
    await screen.findByText('pkg-a')
    expect(screen.getByText('插件包 A')).toBeTruthy()
    expect(screen.queryByText('skill-a')).toBeNull()
    expect(screen.queryByText('mcp-a')).toBeNull()
    expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/list')
  })

  it('启停带目标态：enabled=true 的行点「停用」→ enabled:false', async () => {
    rows()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<InstalledExtensions types={['plugin']} title="已安装插件包" />)
    await screen.findByText('pkg-a')
    fireEvent.click(screen.getByLabelText('切换 pkg-a'))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/extensions/toggle',
      { type: 'plugin', id: 'pkg-a', enabled: false },
    ))
  })

  it('卸载两步：第一次点击只展开确认条，确认才带 force:true 发请求', async () => {
    rows()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<InstalledExtensions types={['plugin']} title="已安装插件包" />)
    await screen.findByText('pkg-a')

    fireEvent.click(screen.getByLabelText('卸载 pkg-a'))
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.click(screen.getByLabelText('确认卸载 pkg-a'))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/extensions/uninstall',
      { type: 'plugin', id: 'pkg-a', force: true },
    ))
  })

  it('excludeIds：已在上层展示的同 id 条目不再重复列出，去重后为空则整块隐藏', async () => {
    api.hubGet.mockResolvedValue({
      ok: true,
      extensions: [
        { ext_id: 'self_reflection', ext_type: 'skill', name: '自省反思', enabled: true },
        { ext_id: 'memory_summary', ext_type: 'skill', name: '记忆摘要', enabled: true },
      ],
    })
    const { container } = render(
      <InstalledExtensions types={['skill']} title="扩展登记" excludeIds={['self_reflection', 'memory_summary']} hideWhenEmpty />,
    )
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/list'))
    await waitFor(() => expect(container.querySelector('[data-testid="installed-ext-skill"]')).toBeNull())
    expect(screen.queryByText('self_reflection')).toBeNull()
  })

  it('配置不是合法 JSON 时不发请求', async () => {
    rows()
    render(<InstalledExtensions types={['plugin']} title="已安装插件包" />)
    await screen.findByText('pkg-a')

    fireEvent.click(screen.getByLabelText('配置 pkg-a'))
    fireEvent.change(screen.getByLabelText('配置文本 pkg-a'), { target: { value: '{bad json' } })
    fireEvent.click(screen.getByLabelText('保存配置 pkg-a'))
    await screen.findByText(/不是合法 JSON/)
    expect(api.hubPost).not.toHaveBeenCalled()
  })
})
