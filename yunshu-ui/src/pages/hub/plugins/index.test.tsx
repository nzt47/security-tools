/**
 * 插件与扩展页测试（2026-10-15 由「清单与配置 / 扩展呈现 两 Tab 容器」合并为单一页面）
 * ------------------------------------------------------------------
 * 锁死的不变量：
 *   - 只有一个导航入口、页内不再有第二个 Tab 条（原 Tab 容器已移除）；
 *   - 声明 client_slot 的插件自动挂载自带 UI（原「扩展呈现」的唯一独有能力，
 *     现挂在清单卡片内）；加载失败必须上屏，不静默；
 *   - 插件清单仍从 /api/plugins 读取。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

const { registerPluginModule } = vi.hoisted(() => ({ registerPluginModule: vi.fn() }))
vi.mock('@/plugins/extensionHost', () => ({ registerPluginModule }))

const MANIFEST = {
  plugins: [
    {
      name: 'demo-ui',
      version: '1.0.0',
      description: '自带界面演示',
      schema: {},
      submit_url: '',
      routes: ['/api/demo/probe'],
      client_slot: { slotId: 'panels', module: '/plugins/demo-ui.js' },
    },
    {
      name: 'memory',
      version: '1.0.0',
      description: '记忆与上下文管理',
      schema: {},
      submit_url: '',
      routes: ['/api/context/config'],
      client_slot: null,
    },
  ],
  host: { python: '3.12', flask: '3.1' },
}

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  const body = url === '/api/plugins' ? MANIFEST : {}
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
  } as unknown as Response
})
vi.stubGlobal('fetch', fetchMock)

const { default: PluginsPage } = await import('./index')

describe('插件与扩展 · 合并后的单一页面', () => {
  beforeEach(() => {
    fetchMock.mockClear()
    registerPluginModule.mockReset()
    registerPluginModule.mockResolvedValue({
      plugin: 'demo-ui',
      module: '/plugins/demo-ui.js',
      state: 'ok',
      entries: [{ id: 'demo-panel', title: '演示面板' }],
    })
  })
  afterEach(cleanup)

  it('页内不再有 Tab 条，标题即「插件与扩展」', async () => {
    render(<PluginsPage />)
    expect(screen.getByRole('heading', { level: 1, name: '插件与扩展' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist', { name: '插件与扩展子模块' })).toBeNull()
    expect(screen.queryAllByRole('tab')).toHaveLength(0)
    await screen.findByTestId('plugin-config-toggle-demo-ui')
  })

  it('声明 client_slot 的插件自动挂载自带 UI（原「扩展呈现」能力）', async () => {
    render(<PluginsPage />)
    await waitFor(() =>
      expect(registerPluginModule).toHaveBeenCalledWith('demo-ui', '/plugins/demo-ui.js', expect.any(Object)),
    )
    expect(await screen.findByText('演示面板')).toBeInTheDocument()
    // 未声明 client_slot 的插件不渲染「插件界面」区块
    expect(screen.queryByTestId('plugin-client-slot-memory')).toBeNull()
  })

  it('自带 UI 加载失败要上屏（不静默）', async () => {
    registerPluginModule.mockResolvedValue({
      plugin: 'demo-ui',
      module: '/plugins/demo-ui.js',
      state: 'error',
      error: '模块未导出 register(registry)',
      entries: [],
    })
    render(<PluginsPage />)
    expect(await screen.findByText(/加载失败：模块未导出 register/)).toBeInTheDocument()
  })
})
