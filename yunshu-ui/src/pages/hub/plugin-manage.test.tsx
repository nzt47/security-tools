/**
 * 工作台「系统组件 / 插件管理」页测试（任务 5 / P1-4：schema 配置表单并入）
 *
 * 覆盖：
 * - 清单渲染：每个插件有「配置」入口；
 * - 无 schema 插件点「配置」→ 优雅降级提示无可配置界面；
 * - 带 schema + submit_url 插件：展开配置面板 → 预填当前值 → POST submit_url 提交成功；
 * - 提交端点 401 → 明确提示「请在『API 令牌』中填入 FLASK_API_TOKEN 后重试」；
 * - 刷新清单（/api/plugins/reload）401 → 同样提示；
 * - 有 schema 无 submit_url → 表单只读预览 + 「暂不支持在线修改」提示。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import PluginManagePage from './plugin-manage'

interface RouteMock {
  match: (url: string, init?: RequestInit) => boolean
  status?: number
  body?: unknown
}

/** 极简 Response 桩：apiClient.request 只消费 ok / status / text() */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

/** 安装 fetch 桩（按 URL + method 路由）；未匹配的请求直接抛错暴露 */
function installFetch(routes: RouteMock[]) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    for (const r of routes) {
      if (r.match(url, init)) {
        const status = r.status ?? 200
        if (status >= 400) {
          return jsonResponse({ error: '未授权：缺少或无效的 API 令牌' }, status)
        }
        return jsonResponse(r.body ?? {}, status)
      }
    }
    throw new Error(`unmocked request: ${init?.method ?? 'GET'} ${url}`)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

const isGetPlugins = (u: string) => u === '/api/plugins'
const isGet = (init?: RequestInit) => (init?.method ?? 'GET') === 'GET'
const isPost = (init?: RequestInit) => init?.method === 'POST'

/** demo 插件 schema（真实 demo_plugin 的字段子集） */
const schemaDemo = {
  type: 'object',
  title: 'Demo 插件配置',
  description: '演示 schema 面板',
  properties: {
    greeting: { type: 'string', title: '问候语', default: '你好，云枢' },
    show_badge: { type: 'boolean', title: '显示徽标', default: true },
  },
  required: ['greeting'],
}

/** 与后端 /api/plugins manifest 同形状的夹具（含无 schema / 有 schema+submit / 有 schema 无 submit 三类） */
const MANIFEST = {
  plugins: [
    {
      name: 'memory',
      version: '1.0.0',
      description: '记忆与上下文管理',
      schema: {},
      submit_url: '',
      routes: ['/api/context/config'],
      client_slot: null,
    },
    {
      name: 'demo',
      version: '1.0.0',
      description: '动态装载演示',
      schema: schemaDemo,
      submit_url: '/api/demo/config',
      routes: ['/api/demo/probe'],
      client_slot: null,
    },
    {
      name: 'safety',
      version: '1.0.0',
      description: '安全、权限与隐私',
      schema: {
        type: 'object',
        properties: {
          alert_level: {
            type: 'string',
            title: '告警级别',
            enum: ['info', 'warning', 'critical'],
            default: 'warning',
          },
        },
      },
      submit_url: '',
      routes: [],
      client_slot: null,
    },
  ],
  host: { python: '3.12', flask: '3.1' },
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('PluginManagePage：插件清单与「配置」入口', () => {
  it('渲染全部插件卡片（每个插件都有配置入口）', async () => {
    installFetch([{ match: isGetPlugins, body: MANIFEST }])
    render(<PluginManagePage />)

    await screen.findByTestId('plugin-config-toggle-memory')
    expect(screen.getByTestId('plugin-config-toggle-demo')).toBeInTheDocument()
    expect(screen.getByTestId('plugin-config-toggle-safety')).toBeInTheDocument()
    // 徽标：仅 schema + submit_url 完整的插件标「可配置」
    expect(screen.getByText('可配置')).toBeInTheDocument()
    expect(screen.getAllByText(/条路由/)).toHaveLength(3)
  })

  it('无 schema 插件点「配置」→ 优雅降级：提示无可配置界面', async () => {
    installFetch([{ match: isGetPlugins, body: MANIFEST }])
    render(<PluginManagePage />)

    fireEvent.click(await screen.findByTestId('plugin-config-toggle-memory'))
    expect(await screen.findByTestId('plugin-no-schema-memory')).toHaveTextContent(
      '该插件暂无可配置界面（未声明 schema）',
    )
    expect(screen.queryByTestId('schema-renderer')).toBeNull()
  })
})

describe('PluginManagePage：schema 配置表单（预填 + 提交）', () => {
  it('带 schema + submit_url：展开 → GET 预填当前值 → POST submit_url → 成功提示', async () => {
    const fetchMock = installFetch([
      { match: isGetPlugins, body: MANIFEST },
      { match: (u, init) => u === '/api/demo/config' && isGet(init), body: { greeting: '当前问候语', show_badge: false } },
      {
        match: (u, init) => u === '/api/demo/config' && isPost(init),
        body: { ok: true, applied: { greeting: '新问候语' }, config: { greeting: '新问候语', show_badge: false } },
      },
    ])
    render(<PluginManagePage />)

    fireEvent.click(await screen.findByTestId('plugin-config-toggle-demo'))
    expect(await screen.findByTestId('plugin-config-panel-demo')).toBeInTheDocument()

    // 值预填：GET submit_url 仅挑选 schema 声明字段
    const input = await screen.findByLabelText('问候语')
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('当前问候语'))

    // 修改并提交 → POST 扁平配置对象
    fireEvent.change(input, { target: { value: '新问候语' } })
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    expect(await screen.findByTestId('plugin-config-msg-demo')).toHaveTextContent('「demo」配置已生效')
    const postCall = fetchMock.mock.calls.find(([, init]) => isPost(init) && String(init?.body).length > 0)
    expect(postCall).toBeTruthy()
    expect(String(postCall?.[0])).toBe('/api/demo/config')
    const body = JSON.parse(String(postCall?.[1]?.body))
    expect(body.greeting).toBe('新问候语')
  })

  it('提交端点 401 → 明确提示需 FLASK_API_TOKEN（与仓库既有文案一致）', async () => {
    installFetch([
      { match: isGetPlugins, body: MANIFEST },
      { match: (u, init) => u === '/api/demo/config' && isGet(init), body: { greeting: 'x' } },
      { match: (u, init) => u === '/api/demo/config' && isPost(init), status: 401 },
    ])
    render(<PluginManagePage />)

    fireEvent.click(await screen.findByTestId('plugin-config-toggle-demo'))
    await screen.findByLabelText('问候语')
    fireEvent.click(screen.getByRole('button', { name: '提交' }))

    const err = await screen.findByTestId('plugin-config-error-demo')
    expect(err).toHaveTextContent('401 未授权')
    expect(err).toHaveTextContent('请在「API 令牌」中填入 FLASK_API_TOKEN 后重试')
  })

  it('有 schema 无 submit_url：只读预览表单 + 提示暂不支持在线修改，且不发起读值请求', async () => {
    const fetchMock = installFetch([{ match: isGetPlugins, body: MANIFEST }])
    render(<PluginManagePage />)

    fireEvent.click(await screen.findByTestId('plugin-config-toggle-safety'))
    expect(await screen.findByTestId('schema-renderer')).toBeInTheDocument()
    expect(screen.getByTestId('plugin-no-submit-safety')).toHaveTextContent('该插件暂不支持在线修改')
    expect(screen.queryByRole('button', { name: '提交' })).toBeNull()
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/api/safety/config'))).toBe(false)
  })
})

describe('PluginManagePage：刷新清单的 401 令牌指引', () => {
  it('POST /api/plugins/reload 401 → 提示需 FLASK_API_TOKEN', async () => {
    installFetch([
      { match: isGetPlugins, body: MANIFEST },
      { match: (u, init) => u === '/api/plugins/reload' && isPost(init), status: 401 },
    ])
    render(<PluginManagePage />)

    await screen.findByTestId('plugin-config-toggle-memory')
    fireEvent.click(screen.getByRole('button', { name: '刷新清单' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('刷新插件清单失败（401 未授权）')
    expect(alert).toHaveTextContent('请在「API 令牌」中填入 FLASK_API_TOKEN 后重试')
  })
})
