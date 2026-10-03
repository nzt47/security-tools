/**
 * 会话 API 客户端（会话任务页 · 多会话 + 会话工作空间）
 * ------------------------------------------------
 * 对接后端 plugins/chat.py（/api/sessions*）：
 *   GET    /api/sessions                        → 会话列表（sessions + current_id）
 *   POST   /api/sessions                        → 新建会话
 *   DELETE /api/sessions/<id>                   → 删除会话（含其工作空间目录）
 *   PUT    /api/sessions/<id>/rename            → 重命名
 *   POST   /api/sessions/current                → 切换当前会话（后端 UI 态）
 *   DELETE /api/sessions/<id>/messages          → 清空该会话消息
 *   GET    /api/sessions/<id>/workspace         → 会话工作空间文件树
 *   POST   /api/sessions/<id>/workspace/reveal  → 在系统文件管理器中打开
 *
 * 统一走 apiClient.request（自动注入 FLASK_API_TOKEN Bearer 头）。
 */
import { request } from './apiClient';

import {
  SESSIONS,
  SESSIONS_CURRENT,
  SESSION_GROUPS,
  WORKSPACES,
  sessionById,
  sessionRename,
  sessionMessages,
  sessionWorkspace,
  sessionWorkspaceReveal,
  sessionWorkspaceRoot,
  sessionGroupSet,
  sessionGroupById,
} from '@/api/endpoints';

export interface SessionMeta {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
  mode?: string;
  timezone?: string | null;
  device_type?: string | null;
  locale?: string | null;
}

export interface SessionListResponse {
  sessions: SessionMeta[];
  current_id: string | null;
}

export interface WorkspaceEntry {
  name: string;
  rel: string;
  type: 'file' | 'dir';
  size: number;
  mtime?: string;
}

export interface SessionWorkspace {
  session_id?: string;
  root: string;
  /** 是否绑定了自定义工作区（默认工作空间为 false） */
  custom?: boolean;
  exists: boolean;
  files: WorkspaceEntry[];
  truncated?: boolean;
}

export interface KnownWorkspace {
  path: string;
  name: string;
  added_at?: string;
}

export interface WorkspaceBindingResult {
  ok: boolean;
  root: string;
  created?: boolean;
  changed?: boolean;
}

export interface SessionGroup {
  id: string;
  name: string;
  created_at?: string;
  count?: number;
}

export interface SessionGroupsResponse {
  groups: SessionGroup[];
  /** 会话 → 分组 归属表（未分组的会话不在其中） */
  membership: Record<string, string>;
}

export const sessionApi = {
  /** 会话列表（按 updated_at 降序） */
  list: () => request<SessionListResponse>(SESSIONS),

  /** 新建会话（可选指定标题） */
  create: (title = '') =>
    request<SessionMeta>(SESSIONS, { method: 'POST', body: { title } }),

  /** 删除会话（后端同时删除会话目录与工作空间） */
  remove: (id: string) =>
    request<{ ok: boolean }>(sessionById(id), { method: 'DELETE' }),

  /** 重命名会话 */
  rename: (id: string, title: string) =>
    request<{ ok: boolean }>(
      sessionRename(id),
      { method: 'PUT', body: { title } },
    ),

  /** 切换后端"当前会话"（影响默认会话归属与历史缓存） */
  setCurrent: (id: string) =>
    request<{ ok: boolean }>(SESSIONS_CURRENT, { method: 'POST', body: { session_id: id } }),

  /** 清空单个会话的消息（保留会话与工作空间） */
  clearMessages: (id: string) =>
    request<{ ok: boolean }>(
      sessionMessages(id),
      { method: 'DELETE' },
    ),

  /** 会话工作空间文件树 */
  workspace: (id: string) =>
    request<SessionWorkspace>(sessionWorkspace(id)),

  /** 在系统文件管理器中打开该会话的工作空间目录 */
  revealWorkspace: (id: string) =>
    request<{ ok: boolean; root?: string; error?: string }>(
      sessionWorkspaceReveal(id),
      { method: 'POST' },
    ),

  /** 绑定/切换会话工作空间到本地绝对路径（仿 DSH 添加工作区） */
  bindWorkspaceRoot: (id: string, path: string, create = false) =>
    request<WorkspaceBindingResult>(
      sessionWorkspaceRoot(id),
      { method: 'PUT', body: { path, create } },
    ),

  /** 恢复会话默认工作空间（移除自定义绑定） */
  unbindWorkspaceRoot: (id: string) =>
    request<WorkspaceBindingResult>(
      sessionWorkspaceRoot(id),
      { method: 'DELETE' },
    ),

  // ──「已添加的工作区」记忆（跨会话复用）────────────────────────

  /** 已添加的工作区列表 */
  listWorkspaces: () => request<{ workspaces: KnownWorkspace[] }>(WORKSPACES),

  /** 登记一个工作区目录（path 绝对路径；create=不存在则创建） */
  addWorkspace: (path: string, create = false) =>
    request<KnownWorkspace>(WORKSPACES, { method: 'POST', body: { path, create } }),

  /** 从记忆列表移除一个工作区（不影响目录） */
  removeWorkspace: (path: string) =>
    request<{ ok: boolean }>(WORKSPACES, { method: 'DELETE', body: { path } }),

  // ── 会话分组（按项目/用途归类） ─────────────────────────────

  /** 全部分组 + 会话归属表 */
  groups: () => request<SessionGroupsResponse>(SESSION_GROUPS),

  /** 新建分组 */
  createGroup: (name: string) =>
    request<SessionGroup>(SESSION_GROUPS, { method: 'POST', body: { name } }),

  /** 重命名分组 */
  renameGroup: (id: string, name: string) =>
    request<{ ok: boolean }>(
      sessionGroupById(id),
      { method: 'PUT', body: { name } },
    ),

  /** 删除分组（组内会话自动变为未分组） */
  deleteGroup: (id: string) =>
    request<{ ok: boolean }>(sessionGroupById(id), { method: 'DELETE' }),

  /** 把会话移入分组；groupId 传 null 移出到「未分组」 */
  assignSessionGroup: (sessionId: string, groupId: string | null) =>
    request<{ ok: boolean }>(
      sessionGroupSet(sessionId),
      { method: 'PUT', body: { group_id: groupId } },
    ),
};

/** 会话列表刷新后，"当前应激活的会话"选择策略：后端 current_id → 最近一次 → 列表首个 */
export function pickSessionId(
  currentId: string | null | undefined,
  sessions: SessionMeta[],
  lastUsedId?: string | null,
): string | null {
  if (currentId && sessions.some((s) => s.id === currentId)) return currentId;
  if (lastUsedId && sessions.some((s) => s.id === lastUsedId)) return lastUsedId;
  return sessions[0]?.id ?? null;
}
