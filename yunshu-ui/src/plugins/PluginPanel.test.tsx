/**
 * PluginPanel 单元测试（任务 T3.3 + T4.2）
 *
 * 覆盖验收点（T3.3）：
 * - 挂载时 GET /api/plugins 并列出全部插件（名称/版本/描述，自解释）；
 * - 选中插件 → 值预填（GET submitUrl 当前生效值）→ SchemaRenderer 渲染表单；
 * - schema 为空 → 「该插件暂无可配置界面」+ routes 列表降级；
 * - 提交：POST 到 submitUrl（声明优先 / 前端映射表兜底）、成功 Toast、提交后刷新；
 * - 无 submit_url → 不渲染提交按钮 + 「该插件暂不支持在线修改」提示；
 * - resolveSubmitUrl 纯函数：声明优先、兜底映射、空串。
 *
 * 覆盖验收点（T4.2）：
 * - 顶部「刷新」按钮 → reloadPlugins()：成功后列表更新（新插件立即可见）+ 成功
 *   Toast；失败 → 错误 Toast + **保留旧列表**；刷新中按钮禁用；
 * - clientSlot 插件显示「加载 UI」按钮；点击调 loadClientUi；成功/失败 Toast，
 *   失败不影响其他功能。
 *
 * fetch 统一桩：按 URL+method 分发（request 依赖全局 fetch）。
 * loadClientUi 经 vi.mock 单独桩化（动态 import 路径在 jsdom 下不可解析，
 * 由本测试验证点击接线与 Toast 行为；模块应用逻辑见 pluginDiscovery.test.ts）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { PluginPanel, resolveSubmitUrl } from './PluginPanel';
import { loadClientUi } from './pluginDiscovery';
import { useChatStore } from '../store/useChatStore';

vi.mock('./pluginDiscovery', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./pluginDiscovery')>();
  return { ...actual, loadClientUi: vi.fn() };
});

/** 简化版 status schema（与真实 schema 的控件类型对齐） */
const STATUS_SCHEMA = {
  type: 'object',
  title: '系统状态与感知',
  properties: {
    refresh_interval: { type: 'integer', title: '刷新频率（秒）', minimum: 1, maximum: 3600, default: 5 },
    planning_enabled: { type: 'boolean', title: '规划引擎', default: true },
    sensor_categories: {
      type: 'array',
      title: '启用的传感器分类',
      items: { type: 'string', enum: ['硬件感知', '网络感知', '进程与行为', '文件感知', '系统与环境'] },
    },
    personality_profile: {
      type: 'string',
      title: '人格方案',
      enum: ['gentle_helper', 'professional', 'humorous', 'custom'],
      default: 'gentle_helper',
    },
    personality_tone: { type: 'number', title: '语气参数', minimum: 0, maximum: 1, default: 0.6 },
  },
  required: ['refresh_interval', 'planning_enabled'],
};

/** /api/status/config GET 返回的「当前生效值」 */
const CURRENT_STATUS = {
  refresh_interval: 10,
  sensor_categories: ['硬件感知', '网络感知'],
  planning_enabled: true,
  personality_profile: 'gentle_helper',
  personality_tone: 0.6,
};

/** wire 清单形状（与后端 manifest 对齐；宽松类型便于构造演示数据） */
interface WireManifest {
  plugins: Record<string, any>[];
  host?: Record<string, string>;
}

const MANIFEST: WireManifest = {
  plugins: [
    {
      name: 'status',
      version: '1.0.0',
      description: '系统状态、感知与性格',
      schema: STATUS_SCHEMA,
      submit_url: '/api/status/config',
      routes: ['/api/status', '/api/status/config'],
    },
    {
      name: 'safety',
      version: '1.0.0',
      description: '安全、权限与隐私',
      schema: {
        type: 'object',
        properties: {
          alert_level: { type: 'string', title: '默认告警级别', enum: ['info', 'warning', 'critical'], default: 'warning' },
        },
      },
      // 无 submit_url（演示「暂不支持在线修改」降级）
      routes: ['/api/safety/keywords', '/api/permission/status'],
    },
    {
      name: 'chat',
      version: '1.0.0',
      description: '对话',
      schema: {},
      routes: ['/api/chat', '/api/sessions'],
    },
  ],
  host: { python: '3.12', flask: '3.0' },
};

/** 含 demo（clientSlot + schema）的清单：T4.2 刷新发现/加载 UI 演示 */
const MANIFEST_WITH_DEMO: WireManifest = {
  plugins: [
    ...MANIFEST.plugins,
    {
      name: 'demo',
      version: '1.0.0',
      description: '动态装载演示',
      schema: {
        type: 'object',
        properties: {
          greeting: { type: 'string', title: '问候语', default: '你好，云枢' },
        },
      },
      submit_url: '/api/demo/config',
      client_slot: { slotId: 'panels', module: '/plugins/demo-ui.js' },
      routes: ['/api/demo/probe', '/api/demo/config'],
    },
  ],
  host: { python: '3.12', flask: '3.0' },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 安装 fetch 桩：/api/plugins + /api/status/config（GET 读 / POST 写） */
function installFetchMock(manifest: WireManifest = MANIFEST) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url === '/api/plugins' && method === 'GET') return jsonResponse(manifest);
    if (url === '/api/status/config' && method === 'GET') return jsonResponse(CURRENT_STATUS);
    if (url === '/api/status/config' && method === 'POST') return jsonResponse({ ok: true });
    return jsonResponse({ ok: false, error: `未桩化: ${method} ${url}` }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock };
}

/**
 * 刷新场景 fetch 桩：POST reload 后 GET 返回 afterReload 清单
 * （模拟「后端新增插件 → 点刷新 → 新插件立即可见」）。
 */
function installRefreshMock(initial: WireManifest, afterReload: WireManifest, reloadStatus = 200) {
  let reloaded = false;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url === '/api/plugins/reload' && method === 'POST') {
      reloaded = true;
      return jsonResponse({ ok: true, plugins: afterReload.plugins }, reloadStatus);
    }
    if (url === '/api/plugins' && method === 'GET') {
      return jsonResponse(reloaded ? afterReload : initial);
    }
    if (url === '/api/status/config' && method === 'GET') return jsonResponse(CURRENT_STATUS);
    return jsonResponse({ ok: false, error: `未桩化: ${method} ${url}` }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock };
}

/** 提取某次 POST 调用的 JSON body */
function postBody(fetchMock: ReturnType<typeof vi.fn>, url: string): Record<string, unknown> | null {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => String(input) === url && (init?.method ?? 'GET').toUpperCase() === 'POST',
  );
  if (!call) return null;
  return JSON.parse(String(call[1]?.body));
}

beforeEach(() => {
  useChatStore.setState({ toasts: [] });
  vi.mocked(loadClientUi).mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('resolveSubmitUrl：提交端点解析', () => {
  it('插件声明 submitUrl 优先', () => {
    expect(resolveSubmitUrl({ name: 'status', version: '1', description: '', schema: {}, routes: [], submitUrl: '/api/status/config' })).toBe('/api/status/config');
  });

  it('未声明时按前端映射表兜底', () => {
    expect(resolveSubmitUrl({ name: 'memory', version: '1', description: '', schema: {}, routes: [] })).toBe('/api/context/config');
  });

  it('声明与映射表均无 → 空串（不支持在线修改）', () => {
    expect(resolveSubmitUrl({ name: 'chat', version: '1', description: '', schema: {}, routes: [] })).toBe('');
  });
});

describe('PluginPanel：列表渲染', () => {
  it('挂载时 GET /api/plugins，左侧列出全部插件（名称 + 描述）', async () => {
    const { fetchMock } = installFetchMock();
    render(<PluginPanel />);
    expect(fetchMock).toHaveBeenCalledWith('/api/plugins', expect.objectContaining({ method: 'GET' }));

    expect(await screen.findByRole('button', { name: 'status' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'safety' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'chat' })).toBeTruthy();
    // 描述（自解释）
    expect(screen.getByText('系统状态、感知与性格')).toBeTruthy();
    expect(screen.getByText('安全、权限与隐私')).toBeTruthy();
    expect(screen.getByText('对话')).toBeTruthy();
  });

  it('含 schema 的插件显示「表单」徽标', async () => {
    installFetchMock();
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'status' });
    expect(screen.getAllByText('表单')).toHaveLength(2); // status + safety
  });

  it('顶部渲染「刷新」按钮', async () => {
    installFetchMock();
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'status' });
    expect(screen.getByTestId('plugin-refresh')).toBeTruthy();
    expect(screen.getByRole('button', { name: '刷新插件清单' })).toBeTruthy();
  });
});

describe('PluginPanel：schema 驱动表单 + 值预填', () => {
  it('默认选中首个插件，GET submitUrl 预填当前值并渲染表单', async () => {
    const { fetchMock } = installFetchMock();
    render(<PluginPanel />);

    // 预填：等待当前生效值落进表单（后端 10，而非 schema 默认 5）——异步预填需 waitFor
    const interval = (await screen.findByDisplayValue('10')) as HTMLInputElement;
    expect(interval).toHaveAttribute('type', 'number');
    expect((screen.getByLabelText('人格方案') as HTMLSelectElement).value).toBe('gentle_helper');
    // 值预填走了一次 GET submitUrl
    expect(
      fetchMock.mock.calls.some(([u, init]) => String(u) === '/api/status/config' && (init?.method ?? 'GET').toUpperCase() === 'GET'),
    ).toBe(true);
  });

  it('修改参数 → 提交 → POST submitUrl 且携带完整表单值', async () => {
    const { fetchMock } = installFetchMock();
    render(<PluginPanel />);
    // 等待值预填完成（确定性的完整表单值基线），再修改
    await screen.findByDisplayValue('10');

    // 修改人格方案 + 刷新频率
    fireEvent.change(screen.getByLabelText('人格方案'), { target: { value: 'humorous' } });
    fireEvent.change(screen.getByLabelText('刷新频率（秒）'), { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      expect(postBody(fetchMock, '/api/status/config')).not.toBeNull();
    });
    const sent = postBody(fetchMock, '/api/status/config')!;
    expect(sent.personality_profile).toBe('humorous');
    expect(sent.refresh_interval).toBe(30);
    // 其余字段随表单一并提交（预填值 + default 填充）
    expect(sent.planning_enabled).toBe(true);
    expect(sent.personality_tone).toBe(0.6);
    expect(sent.sensor_categories).toEqual(['硬件感知', '网络感知']);
  });

  it('提交成功 → 全局 Toast 提示「配置已生效」', async () => {
    const { fetchMock } = installFetchMock();
    render(<PluginPanel />);
    await screen.findByLabelText('人格方案');
    fireEvent.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      const toasts = useChatStore.getState().toasts;
      expect(toasts.some((t) => t.type === 'success' && t.message.includes('「status」配置已生效'))).toBe(true);
    });
    // 提交成功后刷新当前值（第二次 GET submitUrl）
    await waitFor(() => {
      const gets = fetchMock.mock.calls.filter(
        ([u, init]) => String(u) === '/api/status/config' && (init?.method ?? 'GET').toUpperCase() === 'GET',
      );
      expect(gets.length).toBeGreaterThanOrEqual(2);
    });
  });

  it('提交失败（HTTP 错误）→ 错误 Toast', async () => {
    const { fetchMock } = installFetchMock();
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url === '/api/plugins' && method === 'GET') return jsonResponse(MANIFEST);
      if (url === '/api/status/config' && method === 'GET') return jsonResponse(CURRENT_STATUS);
      if (url === '/api/status/config' && method === 'POST') {
        return jsonResponse({ ok: false, error: '校验失败' }, 400);
      }
      return jsonResponse({ ok: false, error: 'not found' }, 404);
    });
    render(<PluginPanel />);
    await screen.findByLabelText('人格方案');
    fireEvent.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      const toasts = useChatStore.getState().toasts;
      expect(toasts.some((t) => t.type === 'error' && t.message.includes('校验失败'))).toBe(true);
    });
  });
});

describe('PluginPanel：刷新（T4.2 运行时发现）', () => {
  it('点「刷新」→ POST reload 后重新拉取 → 新插件立即可见 + 成功 Toast', async () => {
    const { fetchMock } = installRefreshMock(MANIFEST, MANIFEST_WITH_DEMO);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'status' });
    expect(screen.queryByRole('button', { name: 'demo' })).toBeNull();

    fireEvent.click(screen.getByTestId('plugin-refresh'));

    // 新插件出现在列表（无需发版/重启）
    expect(await screen.findByRole('button', { name: 'demo' })).toBeTruthy();
    // 刷新后重新拉取了清单（POST reload 之后 GET）
    const calls = fetchMock.mock.calls.map(([u, init]) => [
      String(u),
      (init?.method ?? 'GET').toUpperCase(),
    ]);
    expect(calls).toContainEqual(['/api/plugins/reload', 'POST']);
    expect(calls.filter(([u, m]) => u === '/api/plugins' && m === 'GET').length).toBeGreaterThanOrEqual(2);
    // 成功 Toast
    await waitFor(() => {
      const toasts = useChatStore.getState().toasts;
      expect(toasts.some((t) => t.type === 'success' && t.message.includes('插件清单已刷新'))).toBe(true);
    });
  });

  it('刷新后新插件 Schema 面板可用（点击新插件 → 表单渲染）', async () => {
    installRefreshMock(MANIFEST, MANIFEST_WITH_DEMO);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'status' });
    fireEvent.click(screen.getByTestId('plugin-refresh'));
    const demoBtn = await screen.findByRole('button', { name: 'demo' });
    fireEvent.click(demoBtn);

    expect(await screen.findByLabelText('问候语')).toBeTruthy();
    expect((screen.getByLabelText('问候语') as HTMLInputElement).value).toBe('你好，云枢');
  });

  it('刷新失败 → 错误 Toast + 保留旧列表（新插件不出现、旧插件仍在）', async () => {
    // POST reload 返回 500 → reloadPlugins 抛错
    installRefreshMock(MANIFEST, MANIFEST_WITH_DEMO, 500);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'status' });

    fireEvent.click(screen.getByTestId('plugin-refresh'));

    // 旧列表保留
    await waitFor(() => {
      const toasts = useChatStore.getState().toasts;
      expect(toasts.some((t) => t.type === 'error' && t.message.includes('刷新插件清单失败'))).toBe(true);
    });
    expect(screen.getByRole('button', { name: 'status' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'safety' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'chat' })).toBeTruthy();
    // 新插件（demo）没有出现
    expect(screen.queryByRole('button', { name: 'demo' })).toBeNull();
  });

  it('刷新中：按钮禁用并显示「刷新中…」', async () => {
    let resolveReload: (() => void) | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url === '/api/plugins/reload' && method === 'POST') {
        await new Promise<void>((r) => {
          resolveReload = r;
        });
        return jsonResponse({ ok: true, plugins: MANIFEST_WITH_DEMO });
      }
      if (url === '/api/plugins' && method === 'GET') return jsonResponse(MANIFEST);
      return jsonResponse({ ok: false, error: `未桩化: ${method} ${url}` }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'status' });

    fireEvent.click(screen.getByTestId('plugin-refresh'));
    await waitFor(() => {
      expect(resolveReload).not.toBeNull();
    });
    const refreshBtn = screen.getByTestId('plugin-refresh') as HTMLButtonElement;
    expect(refreshBtn.disabled).toBe(true);
    expect(refreshBtn.textContent).toBe('刷新中…');

    resolveReload!();
    await waitFor(() => {
      expect((screen.getByTestId('plugin-refresh') as HTMLButtonElement).disabled).toBe(false);
    });
  });
});

describe('PluginPanel：clientSlot 动态装载（T4.2 进阶）', () => {
  it('manifest 声明 client_slot 的插件显示「加载 UI」按钮', async () => {
    installFetchMock(MANIFEST_WITH_DEMO);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'demo' });
    expect(screen.getByTestId('load-ui-demo')).toBeTruthy();
    // 无 clientSlot 的插件不显示
    expect(screen.queryByTestId('load-ui-status')).toBeNull();
  });

  it('点「加载 UI」→ loadClientUi(归一化后的 PluginInfo) → 成功 Toast', async () => {
    installFetchMock(MANIFEST_WITH_DEMO);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'demo' });

    fireEvent.click(screen.getByTestId('load-ui-demo'));

    await waitFor(() => {
      expect(vi.mocked(loadClientUi)).toHaveBeenCalledTimes(1);
    });
    const arg = vi.mocked(loadClientUi).mock.calls[0][0];
    expect(arg.name).toBe('demo');
    expect(arg.clientSlot).toEqual({ slotId: 'panels', module: '/plugins/demo-ui.js' });

    await waitFor(() => {
      const toasts = useChatStore.getState().toasts;
      expect(toasts.some((t) => t.type === 'success' && t.message.includes('「demo」UI 已加载'))).toBe(true);
    });
  });

  it('动态装载失败 → 错误 Toast，不影响其他功能（列表仍在、可继续选择）', async () => {
    vi.mocked(loadClientUi).mockRejectedValue(new Error('module not found'));
    installFetchMock(MANIFEST_WITH_DEMO);
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'demo' });

    fireEvent.click(screen.getByTestId('load-ui-demo'));

    await waitFor(() => {
      const toasts = useChatStore.getState().toasts;
      expect(toasts.some((t) => t.type === 'error' && t.message.includes('「demo」UI 加载失败'))).toBe(true);
    });
    // 列表不受影响
    expect(screen.getByRole('button', { name: 'status' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'demo' })).toBeTruthy();
    // 仍可正常选中查看（其他功能不受影响）
    fireEvent.click(screen.getByRole('button', { name: 'status' }));
    expect(await screen.findByLabelText('人格方案')).toBeTruthy();
  });
});

describe('PluginPanel：空 schema 降级', () => {
  it('schema 为空 → 「该插件暂无可配置界面」+ routes 列表，不渲染表单', async () => {
    installFetchMock();
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'chat' });
    fireEvent.click(screen.getByRole('button', { name: 'chat' }));

    expect(await screen.findByTestId('plugin-no-schema')).toBeTruthy();
    expect(screen.getByText('该插件暂无可配置界面。')).toBeTruthy();
    expect(screen.getByText('暴露的路由：')).toBeTruthy();
    expect(screen.getByText('/api/chat')).toBeTruthy();
    expect(screen.getByText('/api/sessions')).toBeTruthy();
    // 无表单 → 无提交按钮
    expect(screen.queryByRole('button', { name: '提交' })).toBeNull();
  });
});

describe('PluginPanel：无提交端点降级', () => {
  it('有 schema 但无 submit_url → 提示「暂不支持在线修改」，无提交按钮', async () => {
    installFetchMock();
    render(<PluginPanel />);
    await screen.findByRole('button', { name: 'safety' });
    fireEvent.click(screen.getByRole('button', { name: 'safety' }));

    expect(await screen.findByTestId('plugin-no-submit')).toBeTruthy();
    expect(screen.getByText('该插件暂不支持在线修改（未声明提交端点）。')).toBeTruthy();
    // 表单仍在（可查看参数），但没有提交按钮
    expect(screen.getByLabelText('默认告警级别')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '提交' })).toBeNull();
  });
});
