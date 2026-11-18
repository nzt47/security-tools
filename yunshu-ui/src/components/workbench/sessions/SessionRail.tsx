/**
 * SessionRail —— 会话任务页左侧会话栏（仿 DSH 会话界面 + 分组）
 * ------------------------------------------------
 * 布局自上而下：
 *   1. 头部：会话计数 + 「＋ 新建会话」
 *   2. 搜索框
 *   3. 分组工具行：分组列表 + 「＋ 新建分组」（行内输入）
 *   4. 会话列表（按分组分区展示，可收起；未分组会话在「未分组」区）
 *   5. 底部提示
 *
 * 会话行操作：选择 / 任务工作空间 / 移动到分组（浮层菜单）/
 *             重命名（行内输入）/ 删除（两次点击确认）
 * 分组操作：点标题收起展开 / 重命名 / 删除（两次点击确认）
 */
import { useEffect, useRef, useState } from 'react';
import {
  Check,
  ChevronRight,
  Folder,
  FolderInput,
  FolderOpen,
  FolderPlus,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { Badge, Button, Input, ModalBase, Notice, RowButton, Select, Switch } from '@/components/ui';
import type { Project, SessionGroup, SessionMeta } from '../../../lib/sessionApi';

export interface SessionRailProps {
  sessions: SessionMeta[];
  activeId: string | null;
  groups: SessionGroup[];
  /** 会话 → 分组 归属表（未分组会话不在其中） */
  membership: Record<string, string>;
  busy?: boolean;
  error?: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void | Promise<void>;
  onDelete: (id: string) => void | Promise<void>;
  onOpenWorkspace: (id: string) => void;
  onCreateGroup: (name: string) => void | Promise<void>;
  onRenameGroup: (id: string, name: string) => void | Promise<void>;
  onDeleteGroup: (id: string) => void | Promise<void>;
  onAssignGroup: (sessionId: string, groupId: string | null) => void | Promise<void>;
  /** 已建项目（项目选择条；不传 = 无项目可选） */
  projects?: Project[];
  /** 当前项目 id；null = 「未归属项目」视图 */
  activeProjectId?: string | null;
  onSelectProject?: (projectId: string | null) => void;
  /** 新建项目（弹窗提交）；resolve 出项目时自动选中它 */
  onCreateProject?: (input: {
    name: string;
    path: string;
    create: boolean;
  }) => Promise<Project | void> | Project | void;
  /** 把会话移到项目（projectId=null → 未归属项目） */
  onMoveProject?: (sessionId: string, projectId: string | null) => void | Promise<void>;
}

/** 未分组分区专用 key */
const UNGROUPED_KEY = '__ungrouped__';

/** 相对时间展示（刚刚 / N 分钟前 / N 小时前 / N 天前 / 日期） */
export function formatRelTime(iso?: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diffMin = Math.floor((Date.now() - t) / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay} 天前`;
  const d = new Date(t);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 删除二次确认自动复位时长 */
const CONFIRM_MS = 2600;

export function SessionRail({
  sessions,
  activeId,
  groups,
  membership,
  busy = false,
  error = null,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onOpenWorkspace,
  onCreateGroup,
  onRenameGroup,
  onDeleteGroup,
  onAssignGroup,
  projects = [],
  activeProjectId = null,
  onSelectProject,
  onCreateProject,
  onMoveProject,
}: SessionRailProps) {
  const [query, setQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 分组相关
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [groupDraft, setGroupDraft] = useState('');
  const [groupRenameId, setGroupRenameId] = useState<string | null>(null);
  const [groupRenameDraft, setGroupRenameDraft] = useState('');
  const [confirmGroupId, setConfirmGroupId] = useState<string | null>(null);
  const [moveFor, setMoveFor] = useState<string | null>(null);

  // 新建项目弹窗（项目名 + 工作空间绝对路径 + 是否自动创建）
  const [projectOpen, setProjectOpen] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [projectPath, setProjectPath] = useState('');
  const [projectCreate, setProjectCreate] = useState(false);
  const [projectBusy, setProjectBusy] = useState(false);
  const [projectError, setProjectError] = useState<string | null>(null);

  useEffect(() => () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
  }, []);

  // 点击菜单外关闭移动浮层
  useEffect(() => {
    if (!moveFor) return;
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement | null;
      if (!el?.closest('[data-move-menu]')) setMoveFor(null);
    };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [moveFor]);

  // 三段树第一段：只显示当前项目（activeProjectId=null =「未归属项目」）下的会话
  const scoped = sessions.filter((s) => (s.project_id ?? null) === (activeProjectId ?? null));
  const filtered = query.trim()
    ? scoped.filter((s) =>
        `${s.title} ${s.updated_at ?? ''}`.toLowerCase().includes(query.trim().toLowerCase()),
      )
    : scoped;

  const hasGroups = groups.length > 0;
  const groupOf = (id: string) => membership[id] ?? null;
  const ungroupedList = filtered.filter((s) => !groupOf(s.id));
  const inGroup = (gid: string) => filtered.filter((s) => groupOf(s.id) === gid);

  // ── 会话行操作 ─────────────────────────────────────────────

  const armDelete = (id: string) => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    if (confirmId === id) {
      setConfirmId(null);
      void onDelete(id);
      return;
    }
    setConfirmId(id);
    confirmTimer.current = setTimeout(() => setConfirmId(null), CONFIRM_MS);
  };

  const startRename = (s: SessionMeta) => {
    setEditingId(s.id);
    setDraft(s.title);
  };

  const commitRename = () => {
    const id = editingId;
    const title = draft.trim();
    setEditingId(null);
    if (!id || !title) return;
    const original = sessions.find((s) => s.id === id)?.title ?? '';
    if (title === original) return;
    void onRename(id, title);
  };

  const cancelRename = () => setEditingId(null);

  // ── 分组操作 ───────────────────────────────────────────────

  const toggleCollapse = (key: string) =>
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));

  const submitCreateGroup = () => {
    const name = groupDraft.trim();
    setCreatingGroup(false);
    setGroupDraft('');
    if (name) void onCreateGroup(name);
  };

  const armGroupDelete = (id: string) => {
    if (confirmGroupId === id) {
      setConfirmGroupId(null);
      void onDeleteGroup(id);
      return;
    }
    setConfirmGroupId(id);
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    confirmTimer.current = setTimeout(() => setConfirmGroupId(null), CONFIRM_MS);
  };

  const startGroupRename = (g: SessionGroup) => {
    setGroupRenameId(g.id);
    setGroupRenameDraft(g.name);
  };

  const commitGroupRename = () => {
    const id = groupRenameId;
    const name = groupRenameDraft.trim();
    setGroupRenameId(null);
    if (!id || !name) return;
    const original = groups.find((g) => g.id === id)?.name ?? '';
    if (name === original) return;
    void onRenameGroup(id, name);
  };

  const assign = (sessionId: string, groupId: string | null) => {
    setMoveFor(null);
    void onAssignGroup(sessionId, groupId);
  };

  // ── 项目操作 ───────────────────────────────────────────────

  const moveProject = (sessionId: string, projectId: string | null) => {
    setMoveFor(null);
    void onMoveProject?.(sessionId, projectId);
  };

  const openProjectModal = () => {
    setProjectName('');
    setProjectPath('');
    setProjectCreate(false);
    setProjectError(null);
    setProjectOpen(true);
  };

  const closeProjectModal = () => {
    if (projectBusy) return;
    setProjectOpen(false);
    setProjectError(null);
  };

  /** 提交新建项目；成功后由父层刷新项目列表并自动选中该项目 */
  const submitCreateProject = async () => {
    const name = projectName.trim();
    const path = projectPath.trim();
    if (!name || !path) return;
    setProjectBusy(true);
    setProjectError(null);
    try {
      const created = await onCreateProject?.({ name, path, create: projectCreate });
      setProjectOpen(false);
      setProjectName('');
      setProjectPath('');
      setProjectCreate(false);
      if (created && typeof created === 'object' && 'id' in created) {
        onSelectProject?.(created.id);
      }
    } catch (e) {
      setProjectError(e instanceof Error ? e.message : String(e));
    } finally {
      setProjectBusy(false);
    }
  };

  // 会话行（会话列表内联工厂，分区复用）
  const renderSessionRow = (s: SessionMeta) => {
    const active = s.id === activeId;
    const editing = editingId === s.id;
    const armed = confirmId === s.id;
    const currentGroup = groupOf(s.id);
    const currentProject = s.project_id ?? null;

    return (
      <div
        key={s.id}
        data-session-id={s.id}
        className={`group/sess relative flex items-center gap-1 rounded-lg border pr-1 transition-colors ${
          active
            ? 'border-cyan-500/30 bg-cyan-500/12'
            : 'border-transparent hover:border-slate-700/60 hover:bg-slate-800/50'
        }`}
      >
        {/* 主按钮：选择会话 */}
        <Button variant="plain" size="bare"
          type="button"
          onClick={() => onSelect(s.id)}
          title={s.title}
          className="min-w-0 flex-1 py-1.5 pl-2.5 text-left outline-hidden focus-visible:ring-1 focus-visible:ring-primary/60"
        >
          {editing ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commitRename();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancelRename();
                }
              }}
              onClick={(e) => e.stopPropagation()}
              className="w-full rounded-control border border-primary/60 bg-card px-1.5 py-0.5 text-body text-primary outline-hidden"
            />
          ) : (
            <>
              <div
                className={`truncate text-body leading-tight ${
                  active ? 'font-medium text-cyan-200' : 'text-slate-200'
                }`}
              >
                {s.title || '（未命名）'}
              </div>
              <div className="mt-0.5 truncate text-micro text-slate-500">
                {formatRelTime(s.updated_at)} · {s.message_count ?? 0} 条
                {s.agent ? ` · ${s.agent}` : ''}
              </div>
            </>
          )}
        </Button>

        {/* 悬停操作（内联编辑时隐藏） */}
        {!editing && (
          <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/sess:opacity-100 focus-within:opacity-100">
            <Button
              variant="ghost"
              size="xs"
              className="w-6 px-0 text-muted-foreground hover:bg-primary/10 hover:text-primary"
              title="任务工作空间（查看/打开该会话的工作目录）"
              aria-label="打开工作空间"
              onClick={() => onOpenWorkspace(s.id)}
            >
              <FolderOpen size={12} />
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className="w-6 px-0 text-muted-foreground"
              title="移动到分组"
              aria-label="移动到分组"
              onClick={() => setMoveFor(moveFor === s.id ? null : s.id)}
            >
              <FolderInput size={12} />
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className="w-6 px-0 text-muted-foreground hover:text-foreground"
              title="重命名会话"
              aria-label="重命名会话"
              onClick={() => startRename(s)}
            >
              <Pencil size={11} />
            </Button>
            <Button
              variant="ghost"
              size="xs"
              className={`w-6 px-0 ${
                armed
                  ? 'bg-danger/20 text-danger ring-1 ring-danger/60'
                  : 'text-muted-foreground hover:bg-danger/10 hover:text-danger'
              }`}
              title={armed ? '再次点击确认删除' : '删除会话'}
              aria-label="删除会话"
              onClick={() => armDelete(s.id)}
            >
              <Trash2 size={11} />
            </Button>
          </div>
        )}

        {/* 移动到分组浮层 */}
        {moveFor === s.id && (
          <div
            data-move-menu
            className="absolute right-0 top-[calc(100%+2px)] z-50 w-48 rounded-lg border border-slate-700 bg-slate-900 p-1 shadow-2xl"
          >
            <div className="px-2 py-1 text-micro uppercase tracking-[0.14em] text-slate-500">
              移动到分组
            </div>
            <RowButton
              size="sm"
              selected={currentGroup === null}
              onClick={() => assign(s.id, null)}
              className="px-2 py-1.5"
              leading={<span className="flex h-4 w-4 items-center justify-center">{currentGroup === null && <Check size={12} />}</span>}
            >
              未分组
            </RowButton>
            {groups.map((g) => (
              <RowButton
                key={g.id}
                size="sm"
                selected={currentGroup === g.id}
                onClick={() => assign(s.id, g.id)}
                className="px-2 py-1.5"
                leading={<span className="flex h-4 w-4 items-center justify-center">{currentGroup === g.id && <Check size={12} />}</span>}
              >
                <span className="min-w-0 truncate">{g.name}</span>
              </RowButton>
            ))}
            {groups.length === 0 && (
              <div className="px-2 pb-1 pt-0.5 text-micro text-slate-600">
                暂无分组：点击上方「＋ 新建分组」创建后即可归类
              </div>
            )}

            {/* 移到项目：把已有会话归到项目（调用 sessionApi.setProject） */}
            <div className="my-1 border-t border-slate-800" />
            <div className="px-2 py-1 text-micro uppercase tracking-[0.14em] text-slate-500">
              移动到项目
            </div>
            <RowButton
              size="sm"
              selected={currentProject === null}
              onClick={() => moveProject(s.id, null)}
              className="px-2 py-1.5"
              leading={<span className="flex h-4 w-4 items-center justify-center">{currentProject === null && <Check size={12} />}</span>}
            >
              未归属项目
            </RowButton>
            {projects.map((p) => (
              <RowButton
                key={p.id}
                size="sm"
                selected={currentProject === p.id}
                onClick={() => moveProject(s.id, p.id)}
                title={p.workspace_root}
                className="px-2 py-1.5"
                leading={<span className="flex h-4 w-4 items-center justify-center">{currentProject === p.id && <Check size={12} />}</span>}
              >
                <span className="min-w-0 truncate">{p.name}</span>
              </RowButton>
            ))}
            {projects.length === 0 && (
              <div className="px-2 pb-1 pt-0.5 text-micro text-slate-600">
                暂无项目：点击顶部「新建项目」创建后即可归类
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  // 分组分区渲染
  const renderGroupSection = (g: SessionGroup) => {
    const rows = inGroup(g.id);
    const isCollapsed = !!collapsed[g.id];
    const renaming = groupRenameId === g.id;
    const armed = confirmGroupId === g.id;
    return (
      <div key={g.id} data-group-id={g.id} className="space-y-0.5 pt-1">
        <div className="group/g flex items-center gap-1 rounded-lg pr-1 hover:bg-slate-800/50">
          <Button variant="plain" size="bare"
            type="button"
            onClick={() => toggleCollapse(g.id)}
            title={isCollapsed ? '展开分组' : '收起分组'}
            className="flex min-w-0 flex-1 items-center gap-1.5 px-1.5 py-1 text-left outline-hidden"
          >
            <ChevronRight
              size={11}
              className={`shrink-0 text-slate-500 transition-transform ${isCollapsed ? '' : 'rotate-90'}`}
            />
            <span className="shrink-0 text-amber-300/80">
              {isCollapsed ? <Folder size={12} /> : <FolderOpen size={12} />}
            </span>
            {renaming ? (
              <input
                autoFocus
                value={groupRenameDraft}
                onChange={(e) => setGroupRenameDraft(e.target.value)}
                onBlur={commitGroupRename}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitGroupRename();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    setGroupRenameId(null);
                  }
                }}
                onClick={(e) => e.stopPropagation()}
                className="w-full min-w-0 rounded-control border border-primary/60 bg-card px-1.5 py-0.5 text-body text-primary outline-hidden"
              />
            ) : (
              <>
                <span className="min-w-0 flex-1 truncate text-body font-medium text-slate-300">
                  {g.name}
                </span>
                <Badge tone="neutral" size="sm" className="shrink-0 font-mono">
                  {rows.length}
                </Badge>
              </>
            )}
          </Button>
          {!renaming && (
            <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/g:opacity-100 focus-within:opacity-100">
              <Button
                variant="ghost"
                size="xs"
                className="w-6 px-0 text-muted-foreground hover:text-foreground"
                title="重命名分组"
                aria-label="重命名分组"
                onClick={() => startGroupRename(g)}
              >
                <Pencil size={11} />
              </Button>
              <Button
                variant="ghost"
                size="xs"
                className={`w-6 px-0 ${
                  armed
                    ? 'bg-danger/20 text-danger ring-1 ring-danger/60'
                    : 'text-muted-foreground hover:bg-danger/10 hover:text-danger'
                }`}
                title={armed ? '再次点击确认删除（组内会话移回未分组）' : '删除分组'}
                aria-label="删除分组"
                onClick={() => armGroupDelete(g.id)}
              >
                <Trash2 size={11} />
              </Button>
            </div>
          )}
        </div>
        {!isCollapsed && rows.length > 0 && (
          <div className="ml-3 space-y-0.5 border-l border-slate-800/70 pl-1.5">
            {rows.map(renderSessionRow)}
          </div>
        )}
      </div>
    );
  };

  const ungroupedCollapsed = !!collapsed[UNGROUPED_KEY];

  return (
    <aside className="flex h-full min-h-0 w-[260px] shrink-0 flex-col border-r border-slate-800 bg-slate-900/60">
      {/* 头部：标题 + 新建会话 */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 px-3 pb-2.5 pt-3">
        <div className="flex items-center gap-2">
          <MessageSquare size={13} className="text-cyan-400" />
          <span className="text-body font-semibold text-slate-100">会话</span>
          <Badge tone="neutral" size="sm" className="font-mono">
            {scoped.length}
          </Badge>
        </div>
        <Button
          type="button"
          variant="primary"
          size="icon"
          onClick={onCreate}
          title="新建会话（每个会话 = 一个任务，自动拥有独立工作空间）"
          aria-label="新建会话"
        >
          <Plus size={14} />
        </Button>
      </div>

      {/* 项目选择条：当前项目（null = 未归属项目）+ 新建项目 */}
      <div className="shrink-0 border-b border-slate-800/70 px-3 py-2" data-project-bar>
        <div className="flex items-center gap-1.5">
          <Folder size={12} className="shrink-0 text-violet-300/80" />
          <Select
            aria-label="项目"
            title="切换项目：只看该项目下的会话与分组"
            className="min-w-0 flex-1"
            size="xs"
            value={activeProjectId ?? ''}
            onChange={(v) => onSelectProject?.(v || null)}
            placeholder="未归属项目"
            placeholderOptionDisabled={false}
            options={projects.map((p) => ({ value: p.id, label: p.name }))}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={openProjectModal}
            title="新建项目（项目名 + 工作空间绝对路径；可自动创建目录）"
            aria-label="新建项目"
          >
            <FolderPlus size={13} />
          </Button>
        </div>
      </div>

      {/* 搜索 */}
      <div className="shrink-0 px-3 pb-1.5 pt-2">
        {/* 2026-11-17：从原生 input 迁到 ui/Input —— 原语补了 leading/trailing 附加槽，
            图标不再 absolute 压住正文（正文让位 pl-8/pr-8 由原语按尺寸档给）。
            原来这里是全仓唯一"因为缺槽位而保留原生"的输入框（见 handoff §11.135 D）。 */}
        <Input
          type="text"
          size="sm"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索会话…"
          aria-label="搜索会话"
          leading={<Search size={11} />}
          trailing={
            query ? (
              <Button
                variant="ghost"
                size="xs"
                className="h-5 w-5 px-0 text-muted-foreground hover:text-foreground"
                onClick={() => setQuery('')}
                title="清空搜索"
                aria-label="清空搜索"
              >
                <X size={11} />
              </Button>
            ) : undefined
          }
        />
      </div>

      {/* 分组工具行 + 新建分组 */}
      <div className="shrink-0 px-3 pb-0.5">
        <div className="flex items-center justify-between">
          <span className="text-micro font-medium uppercase tracking-[0.18em] text-slate-600">
            分组
          </span>
          <Button
            variant="ghost"
            size="xs"
            className="w-6 px-0 text-muted-foreground"
            title="新建分组（把多个会话归到一个项目/用途下）"
            aria-label="新建分组"
            onClick={() => {
              setCreatingGroup(true);
              setGroupDraft('');
            }}
          >
            <FolderPlus size={12} />
          </Button>
        </div>
        {creatingGroup && (
          <div className="pb-1 pt-1">
            <Input
              autoFocus
              size="xs"
              value={groupDraft}
              onChange={(e) => setGroupDraft(e.target.value)}
              onBlur={submitCreateGroup}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  submitCreateGroup();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setCreatingGroup(false);
                  setGroupDraft('');
                }
              }}
              placeholder="分组名称，回车创建"
              aria-label="分组名称"
            />
          </div>
        )}
      </div>

      {/* 会话列表（按分组分区） */}
      <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-2.5 pb-2.5">
        {error && (
          <div className="mt-1 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-caption leading-relaxed text-rose-200/90">
            {error}
          </div>
        )}
        {busy && scoped.length === 0 ? (
          <div className="flex flex-col items-center gap-2 pt-10 text-slate-500">
            <Loader2 size={15} className="animate-spin text-cyan-400/70" />
            <span className="text-caption">加载会话…</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="px-2 pt-10 text-center text-caption text-slate-500">
            {query ? '无匹配会话' : scoped.length === 0 ? '还没有会话，点击「＋」新建一个' : '—'}
          </div>
        ) : hasGroups ? (
          <div className="space-y-1">
            {/* 分组分区（搜索命中为空的分组隐藏；空分组也保留可收起头部） */}
            {groups.map((g) =>
              query ? (inGroup(g.id).length > 0 ? renderGroupSection(g) : null) : renderGroupSection(g),
            )}
            {/* 未分组分区（有分组时才显式展示） */}
            {ungroupedList.length > 0 && (
              <div data-group-id={UNGROUPED_KEY} className="space-y-0.5 pt-1">
                <div className="flex items-center gap-1.5 rounded-lg px-1.5 py-1 hover:bg-slate-800/50">
                  <Button variant="plain" size="bare"
                    type="button"
                    onClick={() => toggleCollapse(UNGROUPED_KEY)}
                    title={ungroupedCollapsed ? '展开未分组' : '收起未分组'}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left outline-hidden"
                  >
                    <ChevronRight
                      size={11}
                      className={`shrink-0 text-slate-500 transition-transform ${ungroupedCollapsed ? '' : 'rotate-90'}`}
                    />
                    <span className="shrink-0 text-slate-500">
                      <Folder size={12} />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-body font-medium text-slate-400">
                      未分组
                    </span>
                    <Badge tone="neutral" size="sm" className="shrink-0 font-mono">
                      {ungroupedList.length}
                    </Badge>
                  </Button>
                </div>
                {!ungroupedCollapsed && (
                  <div className="ml-3 space-y-0.5 border-l border-slate-800/70 pl-1.5">
                    {ungroupedList.map(renderSessionRow)}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          /* 无分组时的平铺列表 */
          <div className="space-y-1 pt-1">{filtered.map(renderSessionRow)}</div>
        )}
      </div>

      {/* 底部提示 */}
      <div className="shrink-0 border-t border-slate-800 px-3 py-2 text-micro leading-relaxed text-slate-600">
        每个会话 = 一个任务工作空间（自动创建）；用「项目」归类会话与分组
      </div>

      {/* 新建项目弹窗 —— 外壳走 ui/ModalBase（portal + 遮罩 + Esc 捕获 + × 关闭），这里只留内容 */}
      {projectOpen && (
        <ModalBase open onClose={closeProjectModal} title="新建项目" width="max-w-[360px]" closeButton>

            <label className="mb-2 block text-caption text-muted-foreground">
              项目名
              <Input
                autoFocus
                type="text"
                size="sm"
                className="mt-1"
                aria-label="项目名"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !projectBusy) {
                    e.preventDefault();
                    void submitCreateProject();
                  }
                }}
                placeholder="例如：云枢前端"
              />
            </label>

            <label className="mb-2 block text-caption text-muted-foreground">
              工作空间绝对路径
              <Input
                type="text"
                size="sm"
                className="mt-1 [&_input]:font-mono"
                aria-label="工作空间绝对路径"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !projectBusy) {
                    e.preventDefault();
                    void submitCreateProject();
                  }
                }}
                placeholder="例如：C:\Users\you\myproject"
              />
            </label>

            {/* 「自动创建目录」是布尔状态 ⇒ 走 ui/Switch（开=主色高亮 / 关=灰），不再用复选框 */}
            <div className="flex items-center gap-2 text-caption text-muted-foreground">
              <Switch
                size="sm"
                checked={projectCreate}
                onCheckedChange={setProjectCreate}
                label="目录不存在时自动创建"
              />
              <span>目录不存在时自动创建</span>
            </div>

            {projectError && (
              <Notice tone="danger" size="sm" className="mt-2">{projectError}</Notice>
            )}

            <div className="mt-3 flex items-center justify-end gap-1.5">
              <Button variant="default" size="xs" onClick={closeProjectModal}>
                取消
              </Button>
              <Button
                variant="primary"
                size="xs"
                onClick={() => void submitCreateProject()}
                disabled={projectBusy || !projectName.trim() || !projectPath.trim()}
              >
                {projectBusy && <Loader2 size={11} className="animate-spin" />}
                创建项目
              </Button>
            </div>
        </ModalBase>
      )}
    </aside>
  );
}