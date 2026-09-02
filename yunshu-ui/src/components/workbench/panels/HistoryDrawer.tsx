/**
 * HistoryDrawer —— 会话任务页「历史问话」侧滑面板（工作台版）
 * ------------------------------------------------
 * 从 legacy components/Chat/HistoryPanel + HistoryTab + hooks/useHistoryPanel 迁移：
 * - 触发器 → 会话任务页头部按钮（由 WorkbenchChatPage 持有）
 * - 悬停弹出 → 点击展开的右侧滑入抽屉（深色玻璃科技风，与 workbench 面板一致）
 * - 功能：加载 / 搜索 / 复制 / 删除 / 点击跳转定位当前会话中的对应消息
 *
 * 数据源：GET  /api/history?session=…（当前选中会话）
 *         DELETE /api/history/{_real_index}?session=…
 *
 * 跳转定位与 useLayoutStore 消息流配合：
 *   /api/history 与 /api/sessions/{id}/messages 同为后端最近 50 条消息窗口、同序返回，
 *   因此第 j 条历史问答 ⇔ 消息流中第 j 条 role=user 的消息（window ordinal 对齐）。
 *   定位通过 store.setHighlightMsg 标记目标消息 ID，ChatPanel 滚动并高亮后自动清空；
 *   消息流为空（如刚清空）时先尝试加载会话历史，仍无匹配则降级提示。
 *
 * 不变量【不易】：不依赖 legacy Chat 组件的复制/删除实现，逻辑自包含于本文件；
 * 列表展示序为"最新在上"（与旧版 useHistoryPanel 的 reverse 一致）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Check,
  Copy,
  History,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { historyApi, type HistoryEntry } from '../../../lib/historyApi';
import { useLayoutStore } from '../../../stores/useLayoutStore';

/** 复制成功勾选展示时长 */
const COPIED_MS = 1200;

/** 时间展示：当天 HH:mm，跨天 MM-DD HH:mm（与旧版一致） */
function formatHistTime(iso: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const pad = (n: number) => String(n).padStart(2, '0');
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    return sameDay
      ? `${pad(d.getHours())}:${pad(d.getMinutes())}`
      : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return '';
  }
}

/** 剪贴板降级（非安全上下文 / 旧浏览器）：隐藏 textarea + execCommand */
function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export interface HistoryDrawerProps {
  /** 当前选中的会话 ID（空串时走后端默认当前会话） */
  sessionId: string;
  onClose: () => void;
}

export function HistoryDrawer({ sessionId, onClose }: HistoryDrawerProps) {
  // 后端窗口序（旧→新）；展示时 reverse 为"最新在上"，ordinal 保留窗口序号用于跳转定位
  const [items, setItems] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [copiedOrdinal, setCopiedOrdinal] = useState<number | null>(null);
  const [hint, setHint] = useState('');
  const mountedRef = useRef(true);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await historyApi.list({ session: sessionId || undefined });
      if (!mountedRef.current) return;
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [sessionId]);

  // 打开/切换会话 → 拉取该会话的历史问话
  useEffect(() => {
    mountedRef.current = true;
    void loadItems();
    return () => {
      mountedRef.current = false;
      if (copyTimer.current) clearTimeout(copyTimer.current);
      if (hintTimer.current) clearTimeout(hintTimer.current);
    };
  }, [loadItems]);

  // Escape 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  /** 展示行：最新在上；ordinal = 该条目在窗口（历史）序列中的序号，用于定位 */
  const rows = useMemo(() => {
    const ordered = items
      .map((entry, ordinal) => ({ entry, ordinal }))
      .reverse();
    const q = search.trim().toLowerCase();
    if (!q) return ordered;
    return ordered.filter((r) => (r.entry.user || '').toLowerCase().includes(q));
  }, [items, search]);

  const flashHint = (text: string) => {
    setHint(text);
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = setTimeout(() => setHint(''), 3000);
  };

  /**
   * 跳转定位：第 ordinal 条历史问答 ⇔ 消息流第 ordinal 条 user 消息（同窗口同序）。
   * 定位成功 → 关闭抽屉让位给消息区，由 ChatPanel 滚动 + 高亮；否则降级提示。
   */
  const jumpTo = async (ordinal: number) => {
    const st = useLayoutStore.getState();
    const userIds = st.messages.filter((m) => m.role === 'user').map((m) => m.id);
    if (userIds[ordinal]) {
      st.setHighlightMsg(userIds[ordinal]);
      onClose();
      return;
    }
    // 消息流为空（如刚清空对话/刷新后未加载）→ 先加载会话历史再重试一次
    if (sessionId && st.messages.length === 0) {
      await st.loadSessionHistory(sessionId).catch(() => {});
      const after = useLayoutStore
        .getState()
        .messages.filter((m) => m.role === 'user');
      const retryId = after[ordinal]?.id;
      if (retryId) {
        useLayoutStore.getState().setHighlightMsg(retryId);
        onClose();
        return;
      }
    }
    flashHint('未在会话中找到对应消息（可能早于当前加载窗口），仅展示在列表中');
  };

  const handleCopy = async (text: string, ordinal: number) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else if (!legacyCopy(text)) {
        throw new Error('clipboard unavailable');
      }
    } catch {
      console.warn('[HistoryDrawer] 复制失败:', text.slice(0, 40));
      return;
    }
    setCopiedOrdinal(ordinal);
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopiedOrdinal(null), COPIED_MS);
  };

  const handleDelete = async (index: number) => {
    if (!window.confirm('确定要删除这条问话记录吗？')) return;
    try {
      await historyApi.delete(index, sessionId || undefined);
      await loadItems();
      // 若消息区正是该会话的已加载历史（hist-* 消息且无进行中流），
      // 强制同步删除后的会话视图，避免聊天区残留已删条目
      const st = useLayoutStore.getState();
      const histOnly =
        st.messages.length > 0 && st.messages.every((m) => m.id.startsWith('hist-'));
      if (sessionId && !st.streaming && histOnly) {
        void st.loadSessionHistory(sessionId, { force: true });
      }
    } catch (e) {
      const raw = e instanceof Error ? e.message : '删除失败，请重试';
      // 401 → 引导配置 FLASK_API_TOKEN（与 ContextManagerBar 的提示口径一致）
      flashHint(
        raw.includes('401')
          ? '删除失败：写操作需配置 FLASK_API_TOKEN（401）'
          : raw,
      );
    }
  };

  return (
    <div
      className="absolute inset-0 z-40"
      role="dialog"
      aria-modal="true"
      aria-label="历史问话"
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
        className="absolute inset-y-0 right-0 flex w-[360px] max-w-[90%] flex-col border-l border-slate-800 bg-[#0a101f]/95 shadow-[-18px_0_44px_rgba(0,0,0,0.5)] backdrop-blur-xl"
      >
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-cyan-500/30 bg-cyan-500/10 text-cyan-400">
              <History size={13} />
            </div>
            <div className="min-w-0">
              <div className="text-[13px] font-semibold text-slate-100">历史问话</div>
              <div className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-slate-500">
                {loading ? '加载中…' : `${items.length} 条 · 当前会话`}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => void loadItems()}
              title="刷新"
              disabled={loading}
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

        {/* 搜索 */}
        <div className="shrink-0 px-3 pb-2 pt-2.5">
          <div className="relative">
            <Search
              size={12}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索问话…"
              className="w-full rounded-md border border-slate-700/80 bg-slate-900/80 py-1.5 pl-8 pr-7 text-[12px] text-slate-200 outline-none transition-colors placeholder:text-slate-600 focus:border-cyan-600/70"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                title="清空搜索"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-500 hover:text-slate-200"
              >
                <X size={11} />
              </button>
            )}
          </div>
        </div>

        {/* 列表 */}
        <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          {loading ? (
            <div className="flex flex-col items-center gap-2 pt-14 text-slate-500">
              <Loader2 size={16} className="animate-spin text-cyan-400/70" />
              <span className="text-[11.5px]">加载中…</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-2 pt-14 text-slate-500">
              <p className="text-[11.5px] text-rose-400/80">加载失败，请重试</p>
              <button
                type="button"
                onClick={() => void loadItems()}
                className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800"
              >
                <RefreshCw size={11} /> 重试
              </button>
            </div>
          ) : rows.length === 0 ? (
            <div className="pt-14 text-center text-[11.5px] text-slate-500">
              {search ? '无匹配问话' : '暂无历史问话'}
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5 pt-1">
              {rows.map(({ entry, ordinal }) => {
                const text = entry.user || '';
                const preview =
                  text.length > 64 ? `${text.slice(0, 64)}…` : text;
                const copied = copiedOrdinal === ordinal;
                return (
                  <li key={`${entry._real_index}-${ordinal}`}>
                    <div
                      role="button"
                      tabIndex={0}
                      title="点击定位到该条问话"
                      onClick={() => void jumpTo(ordinal)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          void jumpTo(ordinal);
                        }
                      }}
                      className="group cursor-pointer rounded-lg border border-slate-800/80 bg-slate-900/40 px-2.5 py-2 outline-none transition-colors hover:border-cyan-500/35 hover:bg-slate-800/50 focus-visible:border-cyan-500/60"
                    >
                      <div className="flex items-start gap-2">
                        <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-slate-200 group-hover:text-slate-100">
                          {preview}
                        </p>
                        <span
                          role="button"
                          tabIndex={0}
                          title={copied ? '已复制' : '复制'}
                          aria-label="复制"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleCopy(text, ordinal);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              e.stopPropagation();
                              void handleCopy(text, ordinal);
                            }
                          }}
                          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded transition-colors ${
                            copied
                              ? 'text-emerald-400'
                              : 'text-slate-500 opacity-0 group-hover:opacity-100 hover:text-cyan-300'
                          }`}
                        >
                          {copied ? <Check size={11} /> : <Copy size={11} />}
                        </span>
                        <span
                          role="button"
                          tabIndex={0}
                          title="删除此条"
                          aria-label="删除"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleDelete(entry._real_index);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              e.stopPropagation();
                              void handleDelete(entry._real_index);
                            }
                          }}
                          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-slate-500 opacity-0 transition-colors group-hover:opacity-100 hover:bg-rose-500/10 hover:text-rose-400"
                        >
                          <Trash2 size={11} />
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between font-mono text-[9.5px] text-slate-600">
                        <span>{formatHistTime(entry.timestamp)}</span>
                        <span className="text-cyan-500/0 transition-colors group-hover:text-cyan-400/70">
                          定位到消息
                        </span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* 底部提示（跳转降级/删除失败） */}
        {hint && (
          <div className="shrink-0 border-t border-slate-800/80 px-4 py-2 text-[10.5px] text-amber-400/90">
            {hint}
          </div>
        )}
      </motion.aside>
    </div>
  );
}

export default HistoryDrawer;
