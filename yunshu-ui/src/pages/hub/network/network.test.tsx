/**
 * 网络配置页守卫（2026-11-22）
 * ------------------------------------------------------------------
 * 锁死四件事：
 *  1. 不再有「LLM 配置」卡（LLM 只在实例管理）；
 *  2. 字段按**嵌套 schema**（network.* / search.*）读，不是顶层；
 *  3. 保存只提交 network / search 两个分区，且脱敏代理地址不回写；
 *  4. 配置迁移的导入发 config_json（旧写法 {config} 必 400）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

function json(body: unknown, status = 200) {
  return {
    ok: status < 300, status, headers: { get: () => null },
    json: async () => body, text: async () => JSON.stringify(body),
  } as unknown as Response
}

const BASE = {
  llm: { enabled: true, provider: 'deepseek', model: 'deepseek-flash' },
  network: { timeout: 30, max_retries: 3, backoff_factor: 0.5, proxy_enabled: false, proxy_url: '' },
  search: {
    enabled: true, default_engine: 'a', max_results: 10, timeout: 20, cache_ttl: 300,
    engine_priority: ['a', 'b'],
  },
}

let configPayload: typeof BASE
const posts: { url: string; body: Record<string, unknown> }[] = []

const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  const u = String(url)
  if (init?.method === 'POST') {
    let body: Record<string, unknown> = {}
    try { body = JSON.parse(String(init.body ?? '{}')) } catch { body = {} }
    posts.push({ url: u, body })
    if (u.includes('/api/network-config/reset')) return json({ ok: true, config: {} })
    if (u.includes('/api/network-config/import')) return json({ ok: true })
    return json({ ok: true, config: configPayload })
  }
  if (u.includes('/api/search/instances')) {
    return json({ ok: true, instances: [
      { id: 'a', name: '必应', engine_type: 'bing' },
      { id: 'b', name: 'DDG', engine_type: 'duckduckgo' },
    ] })
  }
  if (u.includes('/api/network-config/export')) return json({ ok: true, config_json: '{}' })
  if (u.includes('/api/network-config')) return json(configPayload)
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const NetworkPage = (await import('./index')).default

beforeEach(() => {
  configPayload = JSON.parse(JSON.stringify(BASE))
  posts.length = 0
  fetchMock.mockClear()
})
afterEach(cleanup)

const open = async () => {
  render(<NetworkPage />)
  await waitFor(() => expect(screen.getByTestId('network-timeout')).toBeInTheDocument())
}

describe('网络配置页', () => {
  it('不再有「LLM 配置」卡；按嵌套 schema 读值（network.* / search.*）', async () => {
    await open()
    expect(screen.queryByText('LLM 配置')).toBeNull()
    expect((screen.getByTestId('network-timeout') as HTMLInputElement).value).toBe('30')
    expect((screen.getByTestId('network-retries') as HTMLInputElement).value).toBe('3')
    expect((screen.getByTestId('search-max-results') as HTMLInputElement).value).toBe('10')
    expect((screen.getByTestId('search-timeout') as HTMLInputElement).value).toBe('20')
    expect((screen.getByTestId('search-default-engine') as HTMLSelectElement).value).toBe('a')
    expect(screen.getByText(/当前 2 项/)).toBeInTheDocument()
  })

  it('保存只发 network / search 两个分区（不整份回灌），数字是数字', async () => {
    await open()
    fireEvent.change(screen.getByTestId('network-timeout'), { target: { value: '45' } })
    fireEvent.change(screen.getByTestId('search-max-results'), { target: { value: '20' } })
    fireEvent.click(screen.getByTestId('network-save'))
    await waitFor(() => expect(posts.length).toBe(1))
    const post = posts[0]
    expect(post.url).toContain('/api/network-config')
    expect(Object.keys(post.body).sort()).toEqual(['network', 'search'])
    expect(post.body.network).toEqual({
      timeout: 45, max_retries: 3, backoff_factor: 0.5, proxy_enabled: false, proxy_url: '',
    })
    expect(post.body.search).toEqual({
      enabled: true, default_engine: 'a', max_results: 20, timeout: 20, cache_ttl: 300,
    })
    expect(screen.getByTestId('network-msg').textContent).toContain('已保存并应用')
  })

  it('脱敏代理地址不回写（否则会把真实密码替换成掩码）', async () => {
    configPayload = {
      ...BASE,
      network: { ...BASE.network, proxy_enabled: true, proxy_url: 'http://***@10.0.0.1:8080' },
    }
    await open()
    expect(screen.getByTestId('network-proxy-masked')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('network-save'))
    await waitFor(() => expect(posts.length).toBe(1))
    expect(posts[0].body.network).not.toHaveProperty('proxy_url')
  })

  it('非法数字不发请求，直接点名字段报错', async () => {
    await open()
    fireEvent.change(screen.getByTestId('network-timeout'), { target: { value: '0' } })
    fireEvent.click(screen.getByTestId('network-save'))
    await waitFor(() => expect(screen.getByText(/网络超时 必须是 ≥ 1 的整数/)).toBeInTheDocument())
    expect(posts.length).toBe(0)
  })

  it('配置迁移的导入发 config_json（旧写法 {config} 必 400）', async () => {
    await open()
    fireEvent.change(screen.getByLabelText('网络配置 JSON'), { target: { value: '{"llm":{"enabled":true}}' } })
    fireEvent.click(screen.getByLabelText('确认导入配置'))
    fireEvent.click(screen.getByLabelText('导入网络配置'))
    await waitFor(() => expect(posts.some((p) => p.url.includes('/import'))).toBe(true))
    const imp = posts.find((p) => p.url.includes('/import'))!
    expect(imp.body).toEqual({ config_json: '{"llm":{"enabled":true}}' })
  })

  it('重置要二次确认；取消不发请求，确认才发', async () => {
    await open()
    fireEvent.click(screen.getByTestId('network-reset'))
    const dialog = await screen.findByRole('dialog', { name: '重置网络配置' })
    expect(posts.length).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '重置网络配置' })).toBeNull())
    expect(posts.length).toBe(0)

    fireEvent.click(screen.getByTestId('network-reset'))
    fireEvent.click(await screen.findByRole('button', { name: '重置为默认' }))
    await waitFor(() => expect(posts.some((p) => p.url.includes('/api/network-config/reset'))).toBe(true))
  })
})
