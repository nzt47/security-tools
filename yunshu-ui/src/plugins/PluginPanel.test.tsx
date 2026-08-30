/**
 * PluginPanel 单元测试（任务 T3.3）
 *
 * 覆盖验收点：
 * - 挂载时 GET /api/plugins 并列出全部插件（名称/版本/描述，自解释）；
 * - 选中插件 → 值预填（GET submitUrl 当前生效值）→ SchemaRenderer 渲染表单；
 * - schema 为空 → 「该插件暂无可配置界面」+ routes 列表降级；
 * - 提交：POST 到 submitUrl（声明优先 / 前端映射表兜底）、成功 Toast、提交后刷新；
 * - 无 submit_url → 不渲染提交按钮 + 「该插件暂不支持在线修改」提示；
 * - resolveSubmitUrl 纯函数：声明优先、兜底映射、空串。
 *
 * fetch 统一桩：按 URL+method 分发（request 依赖全局 fetch）。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { PluginPanel, resolveSubmitUrl } from './PluginPanel';
import { useChatStore } from '../store/useChatStore';

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

const MANIFEST = {
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 安装 fetch 桩：/api/plugins + /api/status/config（GET 读 / POST 写） */
function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url === '/api/plugins' && method === 'GET') return jsonResponse(MANIFEST);
    if (url === '/api/status/config' && method === 'GET') return jsonResponse(CURRENT_STATUS);
    if (url === '/api/status/config' && method === 'POST') return jsonResponse({ ok: true });
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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('resolveSubmitUrl：提交端点解析', () => {
  it('插件声明 submit_url 优先', () => {
    expect(resolveSubmitUrl({ name: 'status', version: '1', description: '', schema: {}, routes: [], submit_url: '/api/status/config' })).toBe('/api/status/config');
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
