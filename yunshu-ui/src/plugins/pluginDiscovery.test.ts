/**
 * pluginDiscovery 单元测试（任务 T4.2）
 *
 * 覆盖验收点：
 * - fetchPlugins：GET /api/plugins，归一化 wire 条目（submit_url/client_slot →
 *   submitUrl/clientSlot，空 schema → null，routes 缺省 []，非法条目跳过）；
 * - reloadPlugins：POST /api/plugins/reload 成功后再 GET 拉取；POST 失败抛错
 *   （不继续拉取）；POST 返回 ok:false 抛错；
 * - applyClientModule：register(registry) 约定路径 / 默认导出组件直接挂载路径 /
 *   两者皆无抛错（动态装载失败不影响其他功能）。
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { fetchPlugins, reloadPlugins, applyClientModule, normalizePlugin, PluginInfo } from './pluginDiscovery';
import * as slotRegistry from './slotRegistry';
import { usePanelsStore } from './panelsStore';

/** 后端 wire 形状（snake_case，与 plugins/plugin_api.manifest 对齐） */
const WIRE_PLUGINS = [
  {
    name: 'status',
    version: '1.0.0',
    description: '系统状态与感知',
    schema: { type: 'object', properties: { refresh_interval: { type: 'integer' } } },
    submit_url: '/api/status/config',
    client_slot: null,
    routes: ['/api/status'],
  },
  {
    name: 'demo',
    version: '1.0.0',
    description: '动态装载演示',
    schema: {},
    submit_url: '/api/demo/config',
    client_slot: { slotId: 'panels', module: '/plugins/demo-ui.js' },
    routes: ['/api/demo/probe'],
  },
  {
    name: 'chat',
    version: '1.0.0',
    description: '对话',
    schema: {},
    routes: [],
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url === '/api/plugins' && method === 'GET') {
      return jsonResponse({ plugins: WIRE_PLUGINS, host: { python: '3.12', flask: '3.0' } });
    }
    if (url === '/api/plugins/reload' && method === 'POST') {
      return jsonResponse({ ok: true, plugins: WIRE_PLUGINS });
    }
    return jsonResponse({ ok: false, error: `未桩化: ${method} ${url}` }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  slotRegistry.unmountFromSlot('panels', 'dynamic:demo');
  slotRegistry.loadProfile(slotRegistry.DEFAULT_PROFILE);
  usePanelsStore.setState({ open: {}, initKey: null });
});

describe('normalizePlugin：wire 归一化', () => {
  it('submit_url → submitUrl、client_slot → clientSlot、空 schema → null', () => {
    const p = normalizePlugin(WIRE_PLUGINS[1])!;
    expect(p.name).toBe('demo');
    expect(p.submitUrl).toBe('/api/demo/config');
    expect(p.clientSlot).toEqual({ slotId: 'panels', module: '/plugins/demo-ui.js' });
    expect(p.schema).toBeNull(); // 空 dict → null（前端契约）
  });

  it('缺省字段：routes 缺省 []、submitUrl 空串、无 clientSlot', () => {
    const p = normalizePlugin(WIRE_PLUGINS[2])!;
    expect(p.routes).toEqual([]);
    expect(p.submitUrl).toBe('');
    expect(p.clientSlot).toBeNull();
  });

  it('兼容 camelCase wire（clientSlot / submitUrl）', () => {
    const p = normalizePlugin({
      name: 'camel',
      version: '1',
      description: '',
      schema: null,
      submitUrl: '/api/camel/config',
      clientSlot: { slotId: 'panels', module: '/plugins/camel.js' },
      routes: ['/api/camel'],
    })!;
    expect(p.submitUrl).toBe('/api/camel/config');
    expect(p.clientSlot).toEqual({ slotId: 'panels', module: '/plugins/camel.js' });
  });

  it('非法条目（缺 name / 非对象）→ null', () => {
    expect(normalizePlugin(null)).toBeNull();
    expect(normalizePlugin('x')).toBeNull();
    expect(normalizePlugin({ version: '1' })).toBeNull();
    expect(normalizePlugin([1, 2])).toBeNull();
  });
});

describe('fetchPlugins：GET /api/plugins', () => {
  it('拉取并归一化为 PluginInfo[]', async () => {
    const fetchMock = installFetchMock();
    const list = await fetchPlugins();
    expect(fetchMock).toHaveBeenCalledWith('/api/plugins', expect.objectContaining({ method: 'GET' }));
    expect(list).toHaveLength(3);
    expect(list[0].submitUrl).toBe('/api/status/config');
    expect(list[1].clientSlot?.module).toBe('/plugins/demo-ui.js');
    expect(list[2].submitUrl).toBe('');
  });
});

describe('reloadPlugins：POST /api/plugins/reload 后重新拉取', () => {
  it('先 POST reload 成功，再 GET 拉取最新清单', async () => {
    const fetchMock = installFetchMock();
    const list = await reloadPlugins();
    const calls = fetchMock.mock.calls.map(([input, init]) => [
      String(input),
      (init?.method ?? 'GET').toUpperCase(),
    ]);
    expect(calls).toEqual([
      ['/api/plugins/reload', 'POST'],
      ['/api/plugins', 'GET'],
    ]);
    expect(list).toHaveLength(3);
  });

  it('POST 失败（HTTP 错误）→ 抛错且不再 GET', async () => {
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url === '/api/plugins/reload' && method === 'POST') {
        return jsonResponse({ ok: false, error: '刷新插件清单失败（旧注册表已保留）: boom' }, 500);
      }
      return jsonResponse({ ok: false, error: 'not found' }, 404);
    });

    await expect(reloadPlugins()).rejects.toThrow('刷新插件清单失败');
    // POST 失败 → 不继续拉取清单
    const gets = fetchMock.mock.calls.filter(
      ([u, init]) => String(u) === '/api/plugins' && (init?.method ?? 'GET').toUpperCase() === 'GET',
    );
    expect(gets).toHaveLength(0);
  });

  it('POST 返回 ok:false（200）→ 抛错', async () => {
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url === '/api/plugins/reload' && method === 'POST') {
        return jsonResponse({ ok: false, error: '校验失败' });
      }
      return jsonResponse({ ok: false, error: 'not found' }, 404);
    });
    await expect(reloadPlugins()).rejects.toThrow('校验失败');
  });
});

describe('applyClientModule：动态模块应用约定', () => {
  const DEMO: PluginInfo = {
    name: 'demo',
    version: '1.0.0',
    description: '',
    schema: null,
    routes: [],
    submitUrl: '',
    clientSlot: { slotId: 'panels', module: '/plugins/demo-ui.js' },
  };

  it('导出 register(registry) → 调用 register 并传入注册表面', () => {
    const register = vi.fn();
    applyClientModule({ register }, DEMO);
    expect(register).toHaveBeenCalledTimes(1);
    const facade = register.mock.calls[0][0] as Record<string, unknown>;
    expect(typeof facade.mountToSlot).toBe('function');
    expect(typeof facade.extendProfile).toBe('function');
    expect(typeof facade.openPanel).toBe('function');
    expect(typeof facade.createElement).toBe('function');
  });

  it('仅默认导出组件 → 直接 mountToSlot + extendProfile + openPanel', () => {
    const Comp = () => null;
    applyClientModule({ default: Comp }, DEMO);
    const entries = slotRegistry.getAllSlotEntries('panels');
    const dynamic = entries.find((e) => e.id === 'dynamic:demo');
    expect(dynamic).toBeTruthy();
    expect(dynamic!.component).toBe(Comp);
    expect(usePanelsStore.getState().open['dynamic:demo']).toBe(true);
  });

  it('既无 register 也无默认导出 → 抛错（调用方 Toast，不影响其他功能）', () => {
    expect(() => applyClientModule({ foo: 1 }, DEMO)).toThrow('未导出 register() 或默认组件');
  });
});
