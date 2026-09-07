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
import type { SessionGroup, SessionMeta } from '../../../lib/sessionApi';

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

  const filtered = query.trim()
    ? sessions.filter((s) =>
        `${s.title} ${s.updated_at ?? ''}`.toLowerCase().includes(query.trim().toLowerCase()),
      )
    : sessions;

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

  // 会话行（会话列表内联工厂，分区复用）
  const renderSessionRow = (s: SessionMeta) => {
    const active = s.id === activeId;
    const editing = editingId === s.id;
    const armed = confirmId === s.id;
    const currentGroup = groupOf(s.id);

    return (
      <div
        key={s.id}
        data-session-id={s.id}
        className={`group/sess relative flex items-center gap-1 rounded-lg border pr-1 transition-colors ${
          active
            ? 'border-cyan-500/30 bg-cyan-500/[0.12]'
            : 'border-transparent hover:border-slate-700/60 hover:bg-slate-800/50'
        }`}
      >
        {/* 主按钮：选择会话 */}
        <button
          type="button"
          onClick={() => onSelect(s.id)}
          title={s.title}
          className="min-w-0 flex-1 py-1.5 pl-2.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-cyan-500/60"
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
              className="w-full rounded border border-cyan-600/60 bg-slate-950 px-1.5 py-0.5 text-[12px] text-cyan-100 outline-none"
            />
          ) : (
            <>
              <div
                className={`truncate text-[12px] leading-tight ${
                  active ? 'font-medium text-cyan-200' : 'text-slate-200'
                }`}
              >
                {s.title || '（未命名）'}
              </div>
              <div className="mt-0.5 truncate text-[9.5px] text-slate-500">
                {formatRelTime(s.updated_at)} · {s.message_count ?? 0} 条
              </div>
            </>
          )}
        </button>

        {/* 悬停操作（内联编辑时隐藏） */}
        {!editing && (
          <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/sess:opacity-100 focus-within:opacity-100">
            <button
              type="button"
              title="任务工作空间（查看/打开该会话的工作目录）"
              aria-label="打开工作空间"
              onClick={() => onOpenWorkspace(s.id)}
              className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-cyan-500/10 hover:text-cyan-300"
            >
              <FolderOpen size={12} />
            </button>
            <button
              type="button"
              title="移动到分组"
              aria-label="移动到分组"
              onClick={() => setMoveFor(moveFor === s.id ? null : s.id)}
              className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-violet-500/10 hover:text-violet-300"
            >
              <FolderInput size={12} />
            </button>
            <button
              type="button"
              title="重命名会话"
              aria-label="重命名会话"
              onClick={() => startRename(s)}
              className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-700/70 hover:text-slate-200"
            >
              <Pencil size={11} />
            </button>
            <button
              type="button"
              title={armed ? '再次点击确认删除' : '删除会话'}
              aria-label="删除会话"
              onClick={() => armDelete(s.id)}
              className={`flex h-6 w-6 items-center justify-center rounded-md transition-colors ${
                armed
                  ? 'bg-rose-500/20 text-rose-300 ring-1 ring-rose-500/60'
                  : 'text-slate-500 hover:bg-rose-500/10 hover:text-rose-300'
              }`}
            >
              <Trash2 size={11} />
            </button>
          </div>
        )}

        {/* 移动到分组浮层 */}
        {moveFor === s.id && (
          <div
            data-move-menu
            className="absolute right-0 top-[calc(100%+2px)] z-50 w-48 rounded-lg border border-slate-700 bg-slate-900 p-1 shadow-2xl"
          >
            <div className="px-2 py-1 text-[9.5px] uppercase tracking-[0.14em] text-slate-500">
              移动到分组
            </div>
            <button
              type="button"
              onClick={() => assign(s.id, null)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11.5px] transition-colors hover:bg-slate-800 ${
                currentGroup === null ? 'text-cyan-300' : 'text-slate-300'
              }`}
            >
              <span className="flex h-4 w-4 items-center justify-center">
                {currentGroup === null && <Check size={12} />}
              </span>
              未分组
            </button>
            {groups.map((g) => (
              <button
                key={g.id}
                type="button"
                onClick={() => assign(s.id, g.id)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11.5px] transition-colors hover:bg-slate-800 ${
                  currentGroup === g.id ? 'text-cyan-300' : 'text-slate-300'
                }`}
              >
                <span className="flex h-4 w-4 items-center justify-center">
                  {currentGroup === g.id && <Check size={12} />}
                </span>
                <span className="min-w-0 flex-1 truncate">{g.name}</span>
              </button>
            ))}
            {groups.length === 0 && (
              <div className="px-2 pb-1 pt-0.5 text-[10px] text-slate-600">
                暂无分组：点击上方「＋ 新建分组」创建后即可归类
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
          <button
            type="button"
            onClick={() => toggleCollapse(g.id)}
            title={isCollapsed ? '展开分组' : '收起分组'}
            className="flex min-w-0 flex-1 items-center gap-1.5 px-1.5 py-1 text-left outline-none"
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
                className="w-full min-w-0 rounded border border-cyan-600/60 bg-slate-950 px-1.5 py-0.5 text-[11.5px] text-cyan-100 outline-none"
              />
            ) : (
              <>
                <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-slate-300">
                  {g.name}
                </span>
                <span className="shrink-0 rounded-full bg-slate-800 px-1.5 font-mono text-[9px] text-slate-500">
                  {rows.length}
                </span>
              </>
            )}
          </button>
          {!renaming && (
            <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/g:opacity-100 focus-within:opacity-100">
              <button
                type="button"
                title="重命名分组"
                aria-label="重命名分组"
                onClick={() => startGroupRename(g)}
                className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-700/70 hover:text-slate-200"
              >
                <Pencil size={11} />
              </button>
              <button
                type="button"
                title={armed ? '再次点击确认删除（组内会话移回未分组）' : '删除分组'}
                aria-label="删除分组"
                onClick={() => armGroupDelete(g.id)}
                className={`flex h-6 w-6 items-center justify-center rounded-md transition-colors ${
                  armed
                    ? 'bg-rose-500/20 text-rose-300 ring-1 ring-rose-500/60'
                    : 'text-slate-500 hover:bg-rose-500/10 hover:text-rose-300'
                }`}
              >
                <Trash2 size={11} />
              </button>
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
          <span className="text-[12.5px] font-semibold text-slate-100">会话</span>
          <span className="rounded-full bg-slate-800 px-1.5 py-0.5 font-mono text-[9.5px] text-slate-500">
            {sessions.length}
          </span>
        </div>
        <button
          type="button"
          onClick={onCreate}
          title="新建会话（每个会话 = 一个任务，自动拥有独立工作空间）"
          aria-label="新建会话"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-cyan-600/50 bg-cyan-500/10 text-cyan-300 transition-colors hover:bg-cyan-500/25 hover:text-cyan-200"
        >
          <Plus size={14} />
        </button>
      </div>

      {/* 搜索 */}
      <div className="shrink-0 px-3 pb-1.5 pt-2">
        <div className="relative">
          <Search
            size={11}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-500"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话…"
            className="w-full rounded-md border border-slate-700/80 bg-slate-900/80 py-1.5 pl-7 pr-6 text-[11.5px] text-slate-200 outline-none transition-colors placeholder:text-slate-600 focus:border-cyan-600/70"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              title="清空搜索"
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-500 hover:text-slate-200"
            >
              <X size={11} />
            </button>
          )}
        </div>
      </div>

      {/* 分组工具行 + 新建分组 */}
      <div className="shrink-0 px-3 pb-0.5">
        <div className="flex items-center justify-between">
          <span className="text-[9.5px] font-medium uppercase tracking-[0.18em] text-slate-600">
            分组
          </span>
          <button
            type="button"
            title="新建分组（把多个会话归到一个项目/用途下）"
            aria-label="新建分组"
            onClick={() => {
              setCreatingGroup(true);
              setGroupDraft('');
            }}
            className="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-amber-500/10 hover:text-amber-300"
          >
            <FolderPlus size={12} />
          </button>
        </div>
        {creatingGroup && (
          <div className="pb-1 pt-1">
            <input
              autoFocus
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
              className="w-full rounded-md border border-amber-500/50 bg-slate-950 px-2 py-1 text-[11.5px] text-slate-200 outline-none placeholder:text-slate-600"
            />
          </div>
        )}
      </div>

      {/* 会话列表（按分组分区） */}
      <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-2.5 pb-2.5">
        {error && (
          <div className="mt-1 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[10.5px] leading-relaxed text-rose-200/90">
            {error}
          </div>
        )}
        {busy && sessions.length === 0 ? (
          <div className="flex flex-col items-center gap-2 pt-10 text-slate-500">
            <Loader2 size={15} className="animate-spin text-cyan-400/70" />
            <span className="text-[11px]">加载会话…</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="px-2 pt-10 text-center text-[11px] text-slate-500">
            {query ? '无匹配会话' : sessions.length === 0 ? '还没有会话，点击「＋」新建一个' : '—'}
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
                  <button
                    type="button"
                    onClick={() => toggleCollapse(UNGROUPED_KEY)}
                    title={ungroupedCollapsed ? '展开未分组' : '收起未分组'}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left outline-none"
                  >
                    <ChevronRight
                      size={11}
                      className={`shrink-0 text-slate-500 transition-transform ${ungroupedCollapsed ? '' : 'rotate-90'}`}
                    />
                    <span className="shrink-0 text-slate-500">
                      <Folder size={12} />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-slate-400">
                      未分组
                    </span>
                    <span className="shrink-0 rounded-full bg-slate-800 px-1.5 font-mono text-[9px] text-slate-500">
                      {ungroupedList.length}
                    </span>
                  </button>
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
      <div className="shrink-0 border-t border-slate-800 px-3 py-2 text-[9.5px] leading-relaxed text-slate-600">
        每个会话 = 一个任务工作空间（自动创建）；用「分组」把多个会话归到一个项目
      </div>
    </aside>
  );
}
