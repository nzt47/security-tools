/**
 * 插件与扩展 Tab 容器测试（UI IA 整合阶段 1.2）
 * ------------------------------------------------------------------
 * 锁死的不变量：
 *   - 原「插件管理」与「扩展中心」合并为一个导航项下的两个 Tab，顺序 = 清单与配置 → 扩展呈现；
 *   - 点击 Tab 切换选中态与内容组件（role=tab + aria-selected + 内容重挂载）；
 *   - Tab 选择记忆在 localStorage，非法值回落「清单与配置」；
 *   - 扩展中心不再作为独立自动发现项（该约束由 featureRegistry.test.ts 守卫）。
 *
 * 子模块会发 HTTP 请求 → 统一 stub fetch（返回空数据），只验证容器行为。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'

const fetchMock = vi.fn(async (url: string) => ({
  ok: true,
  status: 200,
  json: async () => (String(url).includes('/api/') ? {} : []),
  text: async () => '',
}))
vi.stubGlobal('fetch', fetchMock)

const { default: PluginsPage, PLUGINS_TABS, PLUGINS_TAB_STORAGE_KEY } = await import('./index')

describe('插件与扩展 · Tab 结构', () => {
  beforeEach(() => {
    localStorage.clear()
    fetchMock.mockClear()
  })
  afterEach(cleanup)

  it('两个子项作为 Tab 并列（清单与配置 → 扩展呈现）', () => {
    render(<PluginsPage />)
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['清单与配置', '扩展呈现'])
    expect(PLUGINS_TABS.map((t) => t.id)).toEqual(['manage', 'extensions'])
  })

  it('默认选中「清单与配置」，渲染容器标题与其内容', async () => {
    render(<PluginsPage />)
    expect(screen.getByRole('tab', { name: /清单与配置/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { level: 1, name: /插件与扩展/ })).toBeInTheDocument()
    // 子模块懒加载：等待其挂载后应发请求
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
  })

  it('点击「扩展呈现」切换选中态、记忆到 localStorage 并挂载原扩展中心内容', async () => {
    render(<PluginsPage />)
    fireEvent.click(screen.getByRole('tab', { name: /扩展呈现/ }))
    expect(screen.getByRole('tab', { name: /扩展呈现/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /清单与配置/ })).toHaveAttribute('aria-selected', 'false')
    expect(localStorage.getItem(PLUGINS_TAB_STORAGE_KEY)).toBe('extensions')
    expect(await screen.findByRole('heading', { level: 1, name: /扩展呈现/ })).toBeInTheDocument()
  })

  it('记忆值合法时重开页面回到上次子模块', () => {
    localStorage.setItem(PLUGINS_TAB_STORAGE_KEY, 'extensions')
    render(<PluginsPage />)
    expect(screen.getByRole('tab', { name: /扩展呈现/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('非法记忆值与非法 initialTab 均回落「清单与配置」', () => {
    localStorage.setItem(PLUGINS_TAB_STORAGE_KEY, 'nope')
    render(<PluginsPage initialTab={'nope' as never} />)
    expect(screen.getByRole('tab', { name: /清单与配置/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('Tab 条提供无障碍语义（tablist + 每项 title 提示）', () => {
    render(<PluginsPage />)
    expect(screen.getByRole('tablist', { name: '插件与扩展子模块' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /清单与配置/ })).toHaveAttribute('title', PLUGINS_TABS[0].hint)
  })
})
