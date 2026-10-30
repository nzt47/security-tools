/**
 * WorkbenchChatPage 集成测试 —— 会话任务页（仿 DSH 双栏会话界面）
 * ------------------------------------------------
 * 覆盖：
 * - 左侧会话栏渲染（列表/当前高亮/新建按钮）
 * - 新建会话（POST /api/sessions → 列表即时出现并激活）
 * - 重命名（行内输入 → PUT rename → 列表标题更新）
 * - 删除（两次点击确认 → DELETE → 列表移除；删除当前会话后落到最近会话）
 * - 任务工作空间抽屉（GET workspace 文件树 + 打开按钮）
 * - 历史问话侧滑面板入口
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import WorkbenchChatPage from './WorkbenchChatPage';
import { useLayoutStore } from '../stores/useLayoutStore';
import type { Project, SessionGroup, SessionMeta } from '../lib/sessionApi';

/**
 * 构造 fetch 桩的 Response。
 *
 * `envelope=true`：按**真实契约**返回统一信封（`X-Envelope: v2` + `{code,data}`）。
 * 【2026-10-30】/api/subagent/list 早已信封化；此前桩返回扁平 {ok,subagents}，
 * 恰好把「组件读顶层 r.subagents」的缺陷盖住（§8：替身照想象写）。
 */
function jsonResponse(body: unknown, ok = true, status = 200, envelope = false) {
  const wire = envelope ? { code: 200, data: body } : body
  return {
    ok,
    status,
    headers: {
      get: (name: string) => (envelope && name.toLowerCase() === 'x-envelope' ? 'v2' : null),
    },
    json: async () => wire,
    text: async () => JSON.stringify(wire),
  } as Response;
}

const SESSION_A = {
  id: 'sess-a',
  title: '会话 A',
  updated_at: '2026-06-27T11:00:00.000Z',
  message_count: 2,
};
const SESSION_B = {
  id: 'sess-b',
  title: '会话 B',
  updated_at: '2026-06-28T11:00:00.000Z',
  message_count: 5,
};

const HISTORY = [
  {
    user: '第一条历史问话',
    Yunshu: '回复一',
    mode: 'normal',
    timestamp: '2026-06-27T11:41:05.983168+08:00',
    _real_index: 0,
  },
];

/** 有状态的会话 mock：支持 list/create/delete/rename/current/workspace/groups */
function createFetchMock() {
  let sessions: SessionMeta[] = [
    { ...SESSION_A } as SessionMeta,
    { ...SESSION_B } as SessionMeta,
  ];
  let currentId: string | null = 'sess-b';
  let groups: SessionGroup[] = [];
  let membership: Record<string, string> = {};
  let groupSeq = 0;
  const boundRoots: Record<string, string> = {};
  let knownWorkspaces: Array<{ path: string; name: string; added_at: string }> = [];
  let projects: Project[] = [];
  let projectSeq = 0;

  return {
    fetchMock: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;

      // ── 项目 ──
      if (url === '/api/projects' && method === 'GET') {
        return jsonResponse({ projects: [...projects] });
      }
      if (url === '/api/projects' && method === 'POST') {
        projectSeq += 1;
        const p: Project = {
          id: `proj-${projectSeq}`,
          name: String(body?.name ?? '新项目'),
          workspace_root: String(body?.path ?? ''),
          created_at: new Date().toISOString(),
        };
        projects = [...projects, p];
        return jsonResponse(p, true, 201);
      }

      // ── 会话分组（支持 ?project_id= 过滤） ──
      if (url.startsWith('/api/session-groups') && method === 'GET') {
        const qs = new URLSearchParams(url.split('?')[1] ?? '');
        const pid = qs.get('project_id');
        const scoped = pid ? groups.filter((g) => g.project_id === pid) : groups;
        return jsonResponse({ groups: scoped, membership: { ...membership } });
      }
      if (url === '/api/session-groups' && method === 'POST') {
        groupSeq += 1;
        const g: SessionGroup = {
          id: `grp-${groupSeq}`,
          name: String(body?.name ?? '新分组'),
          created_at: new Date().toISOString(),
          project_id: (body?.project_id as string | null) ?? null,
        };
        groups = [...groups, g];
        return jsonResponse(g, true, 201);
      }
      const groupMatch = url.match(/^\/api\/session-groups\/([^/]+)$/);
      if (groupMatch) {
        if (method === 'PUT') {
          groups = groups.map((g) =>
            g.id === groupMatch[1] ? { ...g, name: String(body?.name ?? g.name) } : g,
          );
          return jsonResponse({ ok: true });
        }
        if (method === 'DELETE') {
          groups = groups.filter((g) => g.id !== groupMatch[1]);
          const next: Record<string, string> = {};
          for (const [sid, gid] of Object.entries(membership)) {
            if (gid !== groupMatch[1]) next[sid] = gid;
          }
          membership = next;
          return jsonResponse({ ok: true });
        }
      }
      const groupAssign = url.match(/^\/api\/sessions\/([^/]+)\/group$/);
      if (groupAssign && method === 'PUT') {
        const gid = (body?.group_id as string | null) ?? null;
        if (gid) membership[groupAssign[1]] = gid;
        else delete membership[groupAssign[1]];
        return jsonResponse({ ok: true });
      }

      // 会话列表
      if (url === '/api/sessions' && method === 'GET') {
        return jsonResponse({ sessions, current_id: currentId });
      }
      // 新建（沿用请求里的 project_id / agent）
      if (url === '/api/sessions' && method === 'POST') {
        const created = {
          id: 'sess-new',
          title: String(body?.title ?? '新会话'),
          updated_at: new Date().toISOString(),
          message_count: 0,
          project_id: (body?.project_id as string | null) ?? null,
          agent: (body?.agent as string | null) ?? null,
        };
        sessions = [created, ...sessions];
        currentId = created.id;
        return jsonResponse(created, true, 201);
      }
      // 会话归属项目 / 会话智能体
      const projectAssign = url.match(/^\/api\/sessions\/([^/]+)\/project$/);
      if (projectAssign && method === 'PUT') {
        const pid = (body?.project_id as string | null) ?? null;
        sessions = sessions.map((s) => (s.id === projectAssign[1] ? { ...s, project_id: pid } : s));
        return jsonResponse({ ok: true, project_id: pid });
      }
      const agentAssign = url.match(/^\/api\/sessions\/([^/]+)\/agent$/);
      if (agentAssign && method === 'PUT') {
        const agent = (body?.agent as string | null) ?? null;
        sessions = sessions.map((s) => (s.id === agentAssign[1] ? { ...s, agent } : s));
        return jsonResponse({ ok: true, agent });
      }
      // 分身列表（本会话智能体选择器）
      if (url === '/api/subagent/list') {
        return jsonResponse(
          {
            ok: true,
            count: 2,
            subagents: [
              { name: 'sa-1', model_id: 'deepseek-v4-flash' },
              { name: 'sa-2', model_id: 'deepseek-v4-pro' },
            ],
          },
          true,
          200,
          true, // 信封 v2：与服务端一致
        );
      }
      // 切换当前
      if (url === '/api/sessions/current' && method === 'POST') {
        currentId = String(body?.session_id ?? '');
        return jsonResponse({ ok: true });
      }
      // 会话消息
      const msgMatch = url.match(/^\/api\/sessions\/([^/]+)\/messages$/);
      if (msgMatch) {
        if (method === 'GET') return jsonResponse([]);
        if (method === 'DELETE') return jsonResponse({ ok: true });
      }
      // 工作空间（默认目录 vs 绑定目录）
      const wsMatch = url.match(/^\/api\/sessions\/([^/]+)\/workspace$/);
      if (wsMatch && method === 'GET') {
        const sid = wsMatch[1];
        const bound = boundRoots[sid];
        const root = bound ?? `C:/yunshu/data/sessions/${sid}/workspace`;
        const files = bound
          ? [
              { name: 'plan.md', rel: 'plan.md', type: 'file', size: 512, mtime: '2026-07-01T08:00:00' },
              { name: 'src', rel: 'src', type: 'dir', size: 0, mtime: '2026-07-01T08:00:00' },
            ]
          : [
              { name: '任务说明.md', rel: '任务说明.md', type: 'file', size: 128, mtime: '2026-06-28T11:00:00' },
              { name: 'output', rel: 'output', type: 'dir', size: 0, mtime: '2026-06-28T11:00:00' },
            ];
        return jsonResponse({
          session_id: sid,
          root,
          custom: !!bound,
          exists: true,
          files,
          truncated: false,
        });
      }
      // 绑定/切换工作区
      const bindMatch = url.match(/^\/api\/sessions\/([^/]+)\/workspace-root$/);
      if (bindMatch && method === 'PUT') {
        const sid = bindMatch[1];
        const path = String(body?.path ?? '');
        if (path) {
          boundRoots[sid] = path;
          knownWorkspaces = [
            { path, name: path.split(/[\\/]/).pop() ?? path, added_at: new Date().toISOString() },
            ...knownWorkspaces.filter((w) => w.path !== path),
          ];
        } else {
          delete boundRoots[sid];
        }
        return jsonResponse({
          ok: true,
          root: boundRoots[sid] ?? `C:/yunshu/data/sessions/${sid}/workspace`,
          changed: !path,
        });
      }
      if (bindMatch && method === 'DELETE') {
        delete boundRoots[bindMatch[1]];
        return jsonResponse({
          ok: true,
          root: `C:/yunshu/data/sessions/${bindMatch[1]}/workspace`,
          changed: true,
        });
      }
      if (url.endsWith('/workspace/reveal') && method === 'POST') {
        return jsonResponse({ ok: true, root: 'C:/yunshu/data/sessions/x/workspace' });
      }
      // 已添加工作区记忆
      if (url === '/api/workspaces' && method === 'GET') {
        return jsonResponse({ workspaces: knownWorkspaces });
      }
      if (url === '/api/workspaces' && method === 'POST') {
        const path = String(body?.path ?? '');
        const entry = {
          path,
          name: path.split(/[\\/]/).pop() ?? path,
          added_at: new Date().toISOString(),
        };
        knownWorkspaces = [entry, ...knownWorkspaces.filter((w) => w.path !== path)];
        return jsonResponse(entry, true, 201);
      }
      if (url === '/api/workspaces' && method === 'DELETE') {
        knownWorkspaces = knownWorkspaces.filter((w) => w.path !== String(body?.path ?? ''));
        return jsonResponse({ ok: true });
      }
      // 重命名
      const renameMatch = url.match(/^\/api\/sessions\/([^/]+)\/rename$/);
      if (renameMatch && method === 'PUT') {
        sessions = sessions.map((s) =>
          s.id === renameMatch[1] ? { ...s, title: String(body?.title ?? s.title) } : s,
        );
        return jsonResponse({ ok: true });
      }
      // 删除会话
      const delMatch = url.match(/^\/api\/sessions\/([^/]+)$/);
      if (delMatch && method === 'DELETE') {
        sessions = sessions.filter((s) => s.id !== delMatch[1]);
        if (currentId === delMatch[1]) currentId = sessions[0]?.id ?? null;
        return jsonResponse({ ok: true });
      }
      // 历史问话（历史抽屉）
      if (url.startsWith('/api/history')) return jsonResponse(HISTORY);
      // 其余（上下文状态等）
      return jsonResponse({});
    }),
    getSessions: () => sessions,
    getCurrentId: () => currentId,
  };
}

function resetStore() {
  useLayoutStore.setState({
    messages: [],
    thinking: [],
    streaming: false,
    activeStreamId: null,
    highlightMsgId: null,
    activeSessionId: null,
  });
}

describe('WorkbenchChatPage · DSH 式会话界面', () => {
  let state: ReturnType<typeof createFetchMock>;

  beforeEach(() => {
    state = createFetchMock();
    vi.stubGlobal('fetch', state.fetchMock);
    resetStore();
    try {
      localStorage.removeItem('yunshu.session.last');
    } catch {
      /* jsdom ok */
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    resetStore();
    try {
      delete (window as unknown as { electronAPI?: unknown }).electronAPI;
    } catch {
      /* jsdom ok */
    }
  });

  it('渲染左侧会话栏与会话列表（新建按钮 + 会话项）', async () => {
    const { container } = render(<WorkbenchChatPage />);

    expect(await screen.findByRole('button', { name: '新建会话' })).toBeInTheDocument();
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });
    // 当前会话（后端 current_id=sess-b）加载后激活
    await waitFor(() => {
      expect(useLayoutStore.getState().activeSessionId).toBe('sess-b');
    });
  });

  it('正在流式时挂载同一会话 ⇒ 不清空进行中的消息（切面板回来不断会话）', async () => {
    // jsdom 不实现 scrollIntoView：渲染非空消息列表会触发自动滚动
    Element.prototype.scrollIntoView = vi.fn();
    // 模拟"从别的面板切回来"：store 里已有一条正在流式的 assistant 消息
    useLayoutStore.setState({
      streaming: true,
      activeSessionId: 'sess-b',
      messages: [
        { id: 'stream-1', role: 'assistant', content: '正在生成中…', status: 'streaming' },
      ] as never,
    });

    render(<WorkbenchChatPage />);

    await waitFor(() => {
      expect(useLayoutStore.getState().activeSessionId).toBe('sess-b');
    });

    // 关键：boot 没有 clearConversation —— 进行中的消息仍在（修复前会被抹掉，
    // 后续 chunk append 到不存在的 id 上而静默丢失，表现为"切面板就断"）。
    const msgs = useLayoutStore.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0].id).toBe('stream-1');
    expect(msgs[0].content).toBe('正在生成中…');
  });

  it('点击新建会话 → POST /api/sessions 并立即激活新会话', async () => {
    const { container } = render(<WorkbenchChatPage />);

    fireEvent.click(await screen.findByRole('button', { name: '新建会话' }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    const row = await waitFor(() =>
      container.querySelector('[data-session-id="sess-new"]'),
    );
    expect(row).not.toBeNull();
    await waitFor(() => {
      expect(useLayoutStore.getState().activeSessionId).toBe('sess-new');
    });
  });

  it('行内重命名 → PUT rename → 列表标题即时更新', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });

    const rowA = container.querySelector('[data-session-id="sess-a"]') as HTMLElement;
    expect(rowA).not.toBeNull();
    fireEvent.click(within(rowA).getByRole('button', { name: '重命名会话' }));

    const input = within(rowA).getByDisplayValue('会话 A');
    fireEvent.change(input, { target: { value: '架构评审会话' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-a/rename',
        expect.objectContaining({ method: 'PUT' }),
      );
    });
    expect(await screen.findByText('架构评审会话')).toBeInTheDocument();
  });

  it('删除非当前会话需两次点击确认后生效', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });

    const rowA = container.querySelector('[data-session-id="sess-a"]') as HTMLElement;
    const delBtn = within(rowA).getByRole('button', { name: '删除会话' });

    // 第一次点击：进入确认态，不发起请求
    fireEvent.click(delBtn);
    expect(state.fetchMock).not.toHaveBeenCalledWith(
      '/api/sessions/sess-a',
      expect.objectContaining({ method: 'DELETE' }),
    );

    // 第二次点击：确认删除
    fireEvent.click(within(rowA).getByRole('button', { name: '删除会话' }));
    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-a',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).toBeNull();
    });
    expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
  });

  it('删除当前会话后自动落到最近会话', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(useLayoutStore.getState().activeSessionId).toBe('sess-b');
    });

    const rowB = container.querySelector('[data-session-id="sess-b"]') as HTMLElement;
    const delBtn = within(rowB).getByRole('button', { name: '删除会话' });
    fireEvent.click(delBtn);
    fireEvent.click(within(rowB).getByRole('button', { name: '删除会话' }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-b',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
    await waitFor(() => {
      expect(useLayoutStore.getState().activeSessionId).toBe('sess-a');
    });
    expect(container.querySelector('[data-session-id="sess-b"]')).toBeNull();
  });

  it('任务工作空间抽屉：展示会话工作目录文件树', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    // 头部「工作空间」按钮 → 当前会话抽屉
    fireEvent.click(await screen.findByRole('button', { name: '打开任务工作空间' }));

    expect(
      await screen.findByRole('dialog', { name: '任务工作空间' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('任务说明.md')).toBeInTheDocument();
    expect(screen.getByText('output')).toBeInTheDocument();
    expect(screen.getByText(/在文件夹中打开/)).toBeInTheDocument();
  });

  it('历史问话侧滑面板入口可用（按当前会话拉取历史）', async () => {
    render(<WorkbenchChatPage />);

    const trigger = await screen.findByRole('button', { name: /历史问话/ });
    fireEvent.click(trigger);

    expect(
      await screen.findByRole('dialog', { name: '历史问话' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('第一条历史问话')).toBeInTheDocument();
    expect(state.fetchMock).toHaveBeenCalledWith(
      '/api/history?session=sess-b',
      expect.anything(),
    );
  });

  it('新建分组 → POST /api/session-groups，分组标题与未分组分区出现', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
    const input = screen.getByPlaceholderText('分组名称，回车创建');
    fireEvent.change(input, { target: { value: '项目 Alpha' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/session-groups',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(await screen.findByText('项目 Alpha')).toBeInTheDocument();
    // 出现「未分组」分区，A/B 仍在其下
    await waitFor(() => {
      expect(container.querySelector('[data-group-id="__ungrouped__"]')).not.toBeNull();
    });
  });

  it('把会话移入分组 → PUT group，行移到分组容器内', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    // 先创建分组
    fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
    const input = screen.getByPlaceholderText('分组名称，回车创建');
    fireEvent.change(input, { target: { value: '项目 Alpha' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    await screen.findByText('项目 Alpha');

    // 打开会话 A 行的「移动到分组」菜单并选择分组
    const rowA = container.querySelector('[data-session-id="sess-a"]') as HTMLElement;
    fireEvent.click(within(rowA).getByRole('button', { name: '移动到分组' }));
    const menu = container.querySelector('[data-move-menu]') as HTMLElement;
    expect(menu).not.toBeNull();
    fireEvent.click(within(menu).getByRole('button', { name: /项目 Alpha/ }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-a/group',
        expect.objectContaining({ method: 'PUT' }),
      );
    });
    const groupBox = container.querySelector('[data-group-id="grp-1"]') as HTMLElement;
    await waitFor(() => {
      expect(groupBox.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });
    // B 仍在未分组区
    const ungroupedBox = container.querySelector('[data-group-id="__ungrouped__"]') as HTMLElement;
    expect(ungroupedBox.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
  });

  it('删除分组 → DELETE group，组内会话回到未分组', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    // 建组 Alpha（grp-1）与 Beta（grp-2）
    const createGroup = async (name: string) => {
      fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
      const input = screen.getByPlaceholderText('分组名称，回车创建');
      fireEvent.change(input, { target: { value: name } });
      fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
      await screen.findByText(name);
    };
    await createGroup('项目 Alpha');
    await createGroup('项目 Beta');

    // 把 A 移入 Alpha
    const rowA = container.querySelector('[data-session-id="sess-a"]') as HTMLElement;
    fireEvent.click(within(rowA).getByRole('button', { name: '移动到分组' }));
    const menu = container.querySelector('[data-move-menu]') as HTMLElement;
    fireEvent.click(within(menu).getByRole('button', { name: /项目 Alpha/ }));

    const groupBox = container.querySelector('[data-group-id="grp-1"]') as HTMLElement;
    await waitFor(() => {
      expect(groupBox.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });

    // 两次点击确认删除分组 Alpha
    const delBtn = within(groupBox).getByRole('button', { name: '删除分组' });
    fireEvent.click(delBtn);
    fireEvent.click(within(groupBox).getByRole('button', { name: '删除分组' }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/session-groups/grp-1',
        expect.objectContaining({ method: 'DELETE' }),
      );
    });
    await waitFor(() => {
      expect(container.querySelector('[data-group-id="grp-1"]')).toBeNull();
    });
    // 仍有 Beta 分组 → 「未分组」分区存在，A 回到该区
    expect(container.querySelector('[data-group-id="grp-2"]')).not.toBeNull();
    const ungroupedBox = container.querySelector('[data-group-id="__ungrouped__"]') as HTMLElement;
    await waitFor(() => {
      expect(ungroupedBox.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });
  });

  it('添加工作区：绑定自定义目录并展示其文件树', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    // 打开工作空间抽屉：默认工作空间 + 默认文件树
    fireEvent.click(await screen.findByRole('button', { name: '打开任务工作空间' }));
    await screen.findByRole('dialog', { name: '任务工作空间' });
    expect(await screen.findByText('任务说明.md')).toBeInTheDocument();
    expect(screen.getByText('默认工作空间')).toBeInTheDocument();

    // 添加工作区：输入绝对路径 → 绑定
    fireEvent.click(screen.getByRole('button', { name: '添加工作区' }));
    const input = screen.getByPlaceholderText(/输入工作区绝对路径/);
    fireEvent.change(input, { target: { value: 'C:\\Users\\me\\myproject' } });
    fireEvent.click(screen.getByRole('button', { name: '绑定工作区' }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-b/workspace-root',
        expect.objectContaining({ method: 'PUT' }),
      );
    });
    // 绑定后重载文件树：展示新目录内容 + 自定义徽标
    expect(await screen.findByText('plan.md')).toBeInTheDocument();
    expect(screen.getByText('已绑定自定义工作区')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复默认工作空间' })).toBeInTheDocument();
  });

  it('Electron 桌面版「浏览…」调起目录选择器并回填路径', async () => {
    const picker = vi.fn(async () => ({ canceled: false, path: 'C:/picked/project' }));
    (window as unknown as { electronAPI?: unknown }).electronAPI = {
      pickWorkspaceDirectory: picker,
    } as never;

    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    // 打开工作空间抽屉 + 添加工作区表单
    fireEvent.click(await screen.findByRole('button', { name: '打开任务工作空间' }));
    await screen.findByRole('dialog', { name: '任务工作空间' });
    await screen.findByText('任务说明.md');
    fireEvent.click(screen.getByRole('button', { name: '添加工作区' }));

    // Electron 下出现「浏览…」，点击后调用 IPC 选择器并回填路径
    const browse = await screen.findByRole('button', { name: /浏览/ });
    fireEvent.click(browse);
    await waitFor(() => {
      expect(picker).toHaveBeenCalledTimes(1);
    });
    const input = screen.getByPlaceholderText(/输入工作区绝对路径/) as HTMLInputElement;
    await waitFor(() => {
      expect(input.value).toBe('C:/picked/project');
    });
  });

  it('新建项目后自动选中：只显示该项目会话，新建会话带上 project_id', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).not.toBeNull();
    });

    fireEvent.click(screen.getByRole('button', { name: '新建项目' }));
    fireEvent.change(screen.getByLabelText('项目名'), { target: { value: '项目甲' } });
    fireEvent.change(screen.getByLabelText('工作空间绝对路径'), { target: { value: 'C:/work/alpha' } });
    fireEvent.click(screen.getByRole('button', { name: '创建项目' }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/projects',
        expect.objectContaining({ method: 'POST' }),
      );
    });
    // 成功后自动选中新项目 → 无项目会话（sess-a/sess-b）不再显示
    await waitFor(() => {
      expect((screen.getByLabelText('项目') as HTMLSelectElement).value).toBe('proj-1');
    });
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-b"]')).toBeNull();
    });

    // 新建会话沿用当前项目
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    await waitFor(() => {
      const call = state.fetchMock.mock.calls.find(([u, i]) => {
        const init = i as RequestInit | undefined;
        return (
          u === '/api/sessions' &&
          init?.method === 'POST' &&
          JSON.parse(String(init.body)).project_id === 'proj-1'
        );
      });
      expect(call).toBeTruthy();
    });
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-new"]')).not.toBeNull();
    });
  });

  it('会话行「移动到项目」→ PUT setProject，会话移出未归属视图', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });

    // 先建一个项目（自动选中），再切回「未归属项目」看 sess-a
    fireEvent.click(screen.getByRole('button', { name: '新建项目' }));
    fireEvent.change(screen.getByLabelText('项目名'), { target: { value: '项目甲' } });
    fireEvent.change(screen.getByLabelText('工作空间绝对路径'), { target: { value: 'C:/work/alpha' } });
    fireEvent.click(screen.getByRole('button', { name: '创建项目' }));
    await waitFor(() => {
      expect((screen.getByLabelText('项目') as HTMLSelectElement).value).toBe('proj-1');
    });

    fireEvent.change(screen.getByLabelText('项目'), { target: { value: '' } });
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).not.toBeNull();
    });

    const rowA = container.querySelector('[data-session-id="sess-a"]') as HTMLElement;
    fireEvent.click(within(rowA).getByRole('button', { name: '移动到分组' }));
    const menu = container.querySelector('[data-move-menu]') as HTMLElement;
    fireEvent.click(within(menu).getByRole('button', { name: /项目甲/ }));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-a/project',
        expect.objectContaining({ method: 'PUT' }),
      );
    });
    await waitFor(() => {
      expect(container.querySelector('[data-session-id="sess-a"]')).toBeNull();
    });
  });

  it('本会话智能体：选择分身 → PUT /agent，会话列表本地同步显示', async () => {
    const { container } = render(<WorkbenchChatPage />);
    await waitFor(() => {
      expect(useLayoutStore.getState().activeSessionId).toBe('sess-b');
    });

    fireEvent.click(screen.getByRole('button', { name: '智能体：母体' }));
    await waitFor(() => expect(screen.getByText('sa-1')).toBeInTheDocument());
    fireEvent.click(screen.getByText('sa-1'));

    await waitFor(() => {
      expect(state.fetchMock).toHaveBeenCalledWith(
        '/api/sessions/sess-b/agent',
        expect.objectContaining({ method: 'PUT' }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '智能体：sa-1' })).toBeInTheDocument();
    });
    const rowB = container.querySelector('[data-session-id="sess-b"]') as HTMLElement;
    await waitFor(() => expect(within(rowB).getByText(/sa-1/)).toBeInTheDocument());
  });
});
