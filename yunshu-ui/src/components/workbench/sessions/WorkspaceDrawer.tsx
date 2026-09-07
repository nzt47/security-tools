/**
 * WorkspaceDrawer —— 会话任务页「任务工作空间」抽屉
 * ------------------------------------------------
 * 展示/管理当前会话的工作空间（仿 DSH 工作区）：
 * - 默认工作空间：后端自动创建 data/sessions/{id}/workspace
 * - 自定义绑定（「添加工作区」）：把会话绑定到本地任意绝对路径目录
 *   （路径输入 + 不存在时自动创建；已添加目录可快速选择复用）
 *
 * API：
 * - 文件树        GET  /api/sessions/{id}/workspace
 * - 绑定/切换     PUT  /api/sessions/{id}/workspace-root   {path, create}
 * - 恢复默认      DELETE /api/sessions/{id}/workspace-root
 * - 已添加记忆    GET  /api/workspaces
 * - 打开目录      POST /api/sessions/{id}/workspace/reveal
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Check,
  Copy,
  ExternalLink,
  File,
  FileCode2,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Loader2,
  RefreshCw,
  RotateCcw,
  X,
} from 'lucide-react';
import {
  sessionApi,
  type KnownWorkspace,
  type SessionMeta,
  type SessionWorkspace,
} from '../../../lib/sessionApi';
import { copyText } from '../../../utils/clipboard';
import { isElectron } from '../../../electron/types';

export interface WorkspaceDrawerProps {
  /** 目标会话（null = 抽屉关闭/无会话） */
  session: SessionMeta | null;
  onClose: () => void;
}

/** 文件大小可读化 */
function formatSize(bytes: number): string {
  if (!bytes || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/** 按扩展名选图标 */
function fileIcon(name: string) {
  const ext = name.includes('.') ? name.split('.').pop()?.toLowerCase() : '';
  if (['md', 'txt', 'rst', 'log'].includes(ext ?? '')) return <FileText size={13} />;
  if (['json', 'jsonl', 'yaml', 'yml', 'toml', 'ini', 'env'].includes(ext ?? ''))
    return <FileJson size={13} />;
  if (['py', 'js', 'ts', 'tsx', 'jsx', 'c', 'cpp', 'java', 'go', 'rs', 'sh', 'ps1'].includes(ext ?? ''))
    return <FileCode2 size={13} />;
  return <File size={13} />;
}

const COPIED_MS = 1400;

export function WorkspaceDrawer({ session, onClose }: WorkspaceDrawerProps) {
  const [data, setData] = useState<SessionWorkspace | null>(null);
  const [known, setKnown] = useState<KnownWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [bindOpen, setBindOpen] = useState(false);
  const [bindPath, setBindPath] = useState('');
  const [bindCreate, setBindCreate] = useState(false);
  const [binding, setBinding] = useState(false);
  const mountedRef = useRef(true);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flash = (msg: string) => {
    setNotice(msg);
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(null), 3200);
  };

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError(null);
    try {
      const res = await sessionApi.workspace(session.id);
      if (!mountedRef.current) return;
      setData(res);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [session]);

  const loadKnown = useCallback(async () => {
    try {
      const resp = await sessionApi.listWorkspaces();
      if (!mountedRef.current) return;
      setKnown(Array.isArray(resp.workspaces) ? resp.workspaces : []);
    } catch {
      if (mountedRef.current) setKnown([]);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (session) {
      void load();
      void loadKnown();
    }
    return () => {
      mountedRef.current = false;
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
    };
  }, [session, load, loadKnown]);

  // Escape 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleReveal = async () => {
    if (!session) return;
    try {
      const res = await sessionApi.revealWorkspace(session.id);
      flash(res.ok ? `已在文件管理器中打开：${res.root ?? ''}` : (res.error ?? '打开失败'));
    } catch (e) {
      flash(e instanceof Error ? e.message : '打开失败');
    }
  };

  const handleCopyPath = async () => {
    if (!data?.root) return;
    const ok = await copyText(data.root);
    setCopied(ok);
    if (copiedTimer.current) clearTimeout(copiedTimer.current);
    if (ok) copiedTimer.current = setTimeout(() => setCopied(false), COPIED_MS);
    if (!ok) flash('复制失败（浏览器限制）');
  };

  /** 绑定工作区（path 输入 / 已添加目录复用共用） */
  const doBind = async (path: string, create: boolean) => {
    if (!session || !path.trim()) return;
    setBinding(true);
    try {
      const res = await sessionApi.bindWorkspaceRoot(session.id, path.trim(), create);
      setBindOpen(false);
      setBindPath('');
      setBindCreate(false);
      flash(`已绑定工作区：${res.root}`);
      void load();
      void loadKnown();
    } catch (e) {
      flash(e instanceof Error ? e.message : '绑定失败');
    } finally {
      setBinding(false);
    }
  };

  const resetDefault = async () => {
    if (!session) return;
    try {
      const res = await sessionApi.unbindWorkspaceRoot(session.id);
      flash(`已恢复默认工作空间：${res.root}`);
      void load();
    } catch (e) {
      flash(e instanceof Error ? e.message : '恢复失败');
    }
  };

  /** Electron 桌面版：调起系统「选择文件夹」对话框并回填路径 */
  const browseDirectory = async () => {
    if (!session || !window.electronAPI) return;
    try {
      const res = await window.electronAPI.pickWorkspaceDirectory();
      if (!res.canceled && res.path) {
        setBindPath(res.path);
      }
    } catch (e) {
      flash(e instanceof Error ? e.message : '选择目录失败');
    }
  };

  const fileCount = (data?.files ?? []).filter((f) => f.type === 'file').length;
  const dirCount = (data?.files ?? []).filter((f) => f.type === 'dir').length;
  const customBound = data?.custom ?? false;
  const rootMissing = !!data && !data.exists;

  return (
    <div
      className="absolute inset-0 z-40"
      role="dialog"
      aria-modal="true"
      aria-label="任务工作空间"
    >
      {/* 遮罩：点击关闭 */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.16 }}
        onClick={onClose}
        className="absolute inset-0 bg-slate-950/55 backdrop-blur-[1.5px]"
      />

      {/* 面板：从右侧滑入 */}
      <motion.aside
        initial={{ x: '103%' }}
        animate={{ x: 0 }}
        exit={{ x: '103%' }}
        transition={{ type: 'tween', duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
        className="absolute inset-y-0 right-0 flex w-[460px] max-w-[94%] flex-col border-l border-slate-800 bg-[#0a101f]/95 shadow-[-18px_0_44px_rgba(0,0,0,0.5)] backdrop-blur-xl"
      >
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-400">
              <FolderOpen size={13} />
            </div>
            <div className="min-w-0">
              <div className="text-[13px] font-semibold text-slate-100">任务工作空间</div>
              <div className="truncate font-mono text-[9.5px] uppercase tracking-[0.16em] text-slate-500">
                {session ? `会话 · ${session.title}` : '—'}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => void load()}
              title="刷新"
              disabled={loading || !session}
              className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-800 text-slate-400 transition-colors hover:border-cyan-500/40 hover:text-cyan-300 disabled:opacity-40"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              type="button"
              onClick={onClose}
              title="关闭 (Esc)"
              className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-800 text-slate-400 transition-colors hover:border-rose-500/40 hover:text-rose-300"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* 根路径 + 操作 */}
        <div className="shrink-0 space-y-2 border-b border-slate-800 px-4 py-3">
          {data ? (
            <>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-md border border-slate-800 bg-slate-950/80 px-2 py-1.5 font-mono text-[10.5px] text-slate-400">
                  {data.root}
                </code>
                <button
                  type="button"
                  onClick={() => void handleCopyPath()}
                  title={copied ? '已复制' : '复制完整路径'}
                  className="flex h-7 shrink-0 items-center gap-1 rounded-md border border-slate-800 px-2 text-[11px] text-slate-400 transition-colors hover:border-cyan-500/40 hover:text-cyan-300"
                >
                  {copied ? <Check size={12} className="text-cyan-400" /> : <Copy size={12} />}
                  {copied ? '已复制' : '复制路径'}
                </button>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-slate-600">
                  {rootMissing
                    ? '目录当前不可用'
                    : `${fileCount} 个文件 · ${dirCount} 个目录${data.truncated ? '（已达上限截断）' : ''}`}
                </span>
                <button
                  type="button"
                  onClick={() => void handleReveal()}
                  disabled={rootMissing}
                  title="在系统文件管理器中打开该目录（桌面场景）"
                  className="flex items-center gap-1.5 rounded-md border border-cyan-700/50 px-2.5 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:opacity-40"
                >
                  <ExternalLink size={11} />
                  在文件夹中打开
                </button>
              </div>

              {/* 绑定状态 + 添加/切换工作区 */}
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] ${
                    customBound
                      ? 'border border-amber-500/40 bg-amber-500/10 text-amber-300'
                      : 'border border-slate-700 bg-slate-800/70 text-slate-400'
                  }`}
                >
                  {customBound ? '已绑定自定义工作区' : '默认工作空间'}
                </span>
                <button
                  type="button"
                  onClick={() => setBindOpen((v) => !v)}
                  aria-expanded={bindOpen}
                  className="flex items-center gap-1.5 rounded-md border border-cyan-700/50 px-2.5 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
                >
                  <FolderPlus size={11} />
                  {customBound ? '切换工作区' : '添加工作区'}
                </button>
                {customBound && (
                  <button
                    type="button"
                    onClick={() => void resetDefault()}
                    title="解除绑定，恢复为会话默认工作空间"
                    className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200"
                  >
                    <RotateCcw size={11} />
                    恢复默认工作空间
                  </button>
                )}
              </div>

              {/* 添加/切换工作区表单 */}
              {bindOpen && (
                <div className="space-y-2 rounded-lg border border-cyan-800/40 bg-slate-950/60 p-2.5">
                  <div className="flex items-center gap-1.5">
                    <FolderPlus size={12} className="shrink-0 text-cyan-400" />
                    <span className="text-[11px] text-slate-300">
                      绑定本地目录为该会话的工作区（绝对路径）
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      type="text"
                      value={bindPath}
                      onChange={(e) => setBindPath(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !binding) {
                          e.preventDefault();
                          void doBind(bindPath, bindCreate);
                        }
                      }}
                      placeholder="输入工作区绝对路径，如 C:\Users\you\myproject"
                      className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 font-mono text-[11px] text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-600/70"
                    />
                    {isElectron() && window.electronAPI && (
                      <button
                        type="button"
                        onClick={() => void browseDirectory()}
                        disabled={binding}
                        title="打开系统文件夹选择器（Electron 桌面版）"
                        className="flex h-7 shrink-0 items-center gap-1 rounded-md border border-slate-700 px-2 text-[11px] text-slate-300 transition-colors hover:border-cyan-500/50 hover:text-cyan-200 disabled:opacity-40"
                      >
                        <FolderOpen size={11} />
                        浏览…
                      </button>
                    )}
                  </div>
                  <label className="flex cursor-pointer items-center gap-1.5 text-[10.5px] text-slate-400">
                    <input
                      type="checkbox"
                      checked={bindCreate}
                      onChange={(e) => setBindCreate(e.target.checked)}
                      className="accent-cyan-500"
                    />
                    目录不存在时自动创建
                  </label>
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        setBindOpen(false);
                        setBindPath('');
                        setBindCreate(false);
                      }}
                      className="rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      onClick={() => void doBind(bindPath, bindCreate)}
                      disabled={binding || !bindPath.trim()}
                      className="flex items-center gap-1.5 rounded-md border border-cyan-600/60 bg-cyan-500/15 px-2.5 py-1 text-[11px] text-cyan-200 transition-colors hover:bg-cyan-500/25 disabled:opacity-40"
                    >
                      {binding && <Loader2 size={11} className="animate-spin" />}
                      绑定工作区
                    </button>
                  </div>
                  {known.length > 0 && (
                    <div className="pt-0.5">
                      <div className="mb-1 text-[9.5px] uppercase tracking-[0.14em] text-slate-600">
                        已添加的工作区（点击复用）
                      </div>
                      <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto">
                        {known.map((w) => (
                          <button
                            key={w.path}
                            type="button"
                            title={w.path}
                            onClick={() => void doBind(w.path, false)}
                            disabled={binding}
                            className="max-w-full truncate rounded-md border border-slate-700 bg-slate-900 px-2 py-0.5 text-[10px] text-slate-300 transition-colors hover:border-amber-500/50 hover:text-amber-200 disabled:opacity-50"
                          >
                            {w.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {notice && (
                <div className="rounded-md border border-cyan-700/40 bg-cyan-500/10 px-2 py-1 text-[10.5px] text-cyan-200/90">
                  {notice}
                </div>
              )}
            </>
          ) : (
            <div className="flex items-center justify-between">
              <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-slate-600">
                会话工作空间
              </span>
              <button
                type="button"
                onClick={() => void handleReveal()}
                title="在系统文件管理器中打开该目录"
                className="flex items-center gap-1.5 rounded-md border border-cyan-700/50 px-2.5 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
              >
                <ExternalLink size={11} />
                在文件夹中打开
              </button>
            </div>
          )}
        </div>

        {/* 文件树 */}
        <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-2 py-2.5">
          {loading ? (
            <div className="flex flex-col items-center gap-2 pt-14 text-slate-500">
              <Loader2 size={16} className="animate-spin text-cyan-400/70" />
              <span className="text-[11.5px]">读取工作空间…</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-2 pt-14 text-slate-500">
              <p className="text-[11.5px] text-rose-400/80">{error}</p>
              <button
                type="button"
                onClick={() => void load()}
                className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800"
              >
                <RefreshCw size={11} /> 重试
              </button>
            </div>
          ) : rootMissing ? (
            <div className="px-6 pt-14 text-center">
              <Folder size={22} className="mx-auto mb-2 text-rose-400/70" />
              <p className="text-[11.5px] text-slate-300">
                {customBound ? '绑定的工作区目录不存在或已被移动' : '工作空间目录不可用'}
              </p>
              <p className="mt-1 text-[10.5px] leading-relaxed text-slate-600">
                {customBound
                  ? '可重新「切换工作区」指向正确目录，或「恢复默认工作空间」。'
                  : '刷新后仍不可用，可尝试「添加工作区」绑定其他目录。'}
              </p>
            </div>
          ) : (data?.files ?? []).length === 0 ? (
            <div className="px-6 pt-14 text-center">
              <Folder size={22} className="mx-auto mb-2 text-slate-700" />
              <p className="text-[11.5px] text-slate-500">工作空间是空的</p>
              <p className="mt-1 text-[10.5px] leading-relaxed text-slate-600">
                会话产生的任务文件会存放在此目录。
                <br />
                也可点击上方「在文件夹中打开」直接查看，
                <br />
                或「添加工作区」绑定到已有项目目录。
              </p>
            </div>
          ) : (
            <ul className="flex flex-col">
              {(data?.files ?? []).map((f) => {
                const depth = f.rel.split('/').length - 1;
                const isDir = f.type === 'dir';
                return (
                  <li
                    key={f.rel}
                    title={f.rel}
                    className="flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1 hover:bg-slate-800/50"
                    style={{ paddingLeft: `${12 + depth * 14}px` }}
                  >
                    <span
                      className={`shrink-0 ${isDir ? 'text-amber-300/80' : 'text-slate-500'}`}
                    >
                      {isDir ? <Folder size={13} /> : fileIcon(f.name)}
                    </span>
                    <span
                      className={`min-w-0 flex-1 truncate text-[11.5px] ${
                        isDir ? 'text-slate-200' : 'text-slate-400'
                      }`}
                    >
                      {f.name}
                    </span>
                    {!isDir && f.size > 0 && (
                      <span className="shrink-0 font-mono text-[9.5px] text-slate-600">
                        {formatSize(f.size)}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* 底部说明 */}
        <div className="shrink-0 border-t border-slate-800 px-4 py-2 text-[9.5px] text-slate-600">
          默认目录：data/sessions/&lt;会话ID&gt;/workspace；可绑定本地任意项目目录（删除会话时仅清理默认目录）
        </div>
      </motion.aside>
    </div>
  );
}
