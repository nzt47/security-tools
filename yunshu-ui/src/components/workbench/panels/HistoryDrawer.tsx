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
import { Button, Input } from '@/components/ui';
import { historyApi, type HistoryEntry } from '../../../lib/historyApi';
import {
  downloadSession,
  getOutline,
  getStats,
  type SessionOutline,
  type SessionStats,
} from '../../../lib/sessionInsightApi';
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
  // E1-4c：会话洞察（大纲 / 统计）+ 导出
  const [insight, setInsight] = useState<'none' | 'outline' | 'stats'>('none');
  const [outlineDoc, setOutlineDoc] = useState<SessionOutline | null>(null);
  const [statsDoc, setStatsDoc] = useState<SessionStats | null>(null);
  const [insightBusy, setInsightBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
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

  /** E1-4c：切换大纲/统计（首次点开才拉数据，之后复用） */
  const toggleInsight = async (kind: 'outline' | 'stats') => {
    if (insight === kind) {
      setInsight('none');
      return;
    }
    setInsight(kind);
    if (!sessionId) return;
    setInsightBusy(true);
    try {
      if (kind === 'outline' && !outlineDoc) setOutlineDoc(await getOutline(sessionId));
      if (kind === 'stats' && !statsDoc) setStatsDoc(await getStats(sessionId));
    } catch {
      flashHint(kind === 'outline' ? '读取轮次大纲失败' : '读取统计失败');
    } finally {
      setInsightBusy(false);
    }
  };

  /** E1-4c：导出会话（md / zip；带 Bearer 令牌，文件名由服务端派生） */
  const handleExport = async (format: 'md' | 'zip') => {
    if (!sessionId || exportBusy) return;
    setExportBusy(true);
    try {
      const name = await downloadSession(sessionId, format);
      flashHint(`已导出 ${name}`);
    } catch (e) {
      flashHint(e instanceof Error ? e.message : '导出失败');
    } finally {
      setExportBusy(false);
    }
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
              <div className="text-body-lg font-semibold text-slate-100">历史问话</div>
              <div className="font-mono text-micro uppercase tracking-[0.16em] text-slate-500">
                {loading ? '加载中…' : `${items.length} 条 · 当前会话`}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => void loadItems()}
              title="刷新"
              aria-label="刷新"
              disabled={loading}
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={onClose}
              title="关闭 (Esc)"
              aria-label="关闭"
            >
              <X size={14} />
            </Button>
          </div>
        </div>

        {/* E1-4c：会话洞察 + 导出（大纲 / 统计 / 导出 md / 导出 zip） */}
        <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-slate-800 px-3 py-1.5">
          <Button
            type="button" variant="ghost" size="xs"
            aria-pressed={insight === 'outline'}
            data-testid="session-outline-toggle"
            onClick={() => void toggleInsight('outline')}
            title="按轮查看：用户说了什么 / 调了哪些工具 / 结论一句"
          >
            大纲
          </Button>
          <Button
            type="button" variant="ghost" size="xs"
            aria-pressed={insight === 'stats'}
            data-testid="session-stats-toggle"
            onClick={() => void toggleInsight('stats')}
            title="每轮 tokens / 工具数 / 汇总"
          >
            统计
          </Button>
          <span className="flex-1" />
          <Button
            type="button" variant="ghost" size="xs"
            data-testid="session-export-md"
            disabled={!sessionId || exportBusy}
            onClick={() => void handleExport('md')}
            title="导出为 Markdown（文件名由服务端按 sha256 派生）"
          >
            导出 MD
          </Button>
          <Button
            type="button" variant="ghost" size="xs"
            data-testid="session-export-zip"
            disabled={!sessionId || exportBusy}
            onClick={() => void handleExport('zip')}
            title="导出为 ZIP（manifest.json + transcript.md + messages.jsonl）"
          >
            导出 ZIP
          </Button>
        </div>

        {/* E1-4c：洞察面板（大纲 / 统计） */}
        {insight !== 'none' && (
          <div
            className="wb-think-scroll max-h-56 shrink-0 overflow-y-auto border-b border-slate-800 px-3 py-2"
            data-testid="session-insight-panel"
          >
            {insightBusy && (
              <div className="flex items-center gap-1.5 text-micro text-slate-500">
                <Loader2 size={11} className="animate-spin" /> 读取中…
              </div>
            )}
            {insight === 'outline' && outlineDoc && (
              <ol className="flex flex-col gap-1.5">
                {outlineDoc.rounds.map((row) => (
                  <li key={row.round} className="rounded border border-slate-800/70 px-2 py-1">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-micro text-cyan-400">#{row.round}</span>
                      <span className="min-w-0 flex-1 truncate text-micro text-slate-300" title={row.user}>
                        {row.user || '（无用户消息）'}
                      </span>
                    </div>
                    {row.tools.length > 0 && (
                      <div className="mt-0.5 truncate text-micro text-slate-500" title={row.tools.join('、')}>
                        工具：{row.tools.join('、')}
                      </div>
                    )}
                    {row.conclusion && (
                      <div className="mt-0.5 truncate text-micro text-slate-500" title={row.conclusion}>
                        {row.conclusion}
                      </div>
                    )}
                  </li>
                ))}
                {outlineDoc.rounds.length === 0 && (
                  <li className="text-micro text-slate-500">还没有可列出的一轮</li>
                )}
              </ol>
            )}
            {insight === 'stats' && statsDoc && (
              <div className="flex flex-col gap-1">
                <div className="text-micro text-slate-400">
                  {statsDoc.totals.rounds} 轮 · 进 {statsDoc.totals.tokens_in} tok ·
                  出 {statsDoc.totals.tokens_out} tok · 工具 {statsDoc.totals.tools} 次
                </div>
                {statsDoc.rounds.map((row) => (
                  <div key={row.round} className="flex items-baseline gap-2 text-micro text-slate-400">
                    <span className="font-mono text-cyan-400">#{row.round}</span>
                    <span className="flex-1 truncate font-mono">
                      进 {row.tokens_in} · 出 {row.tokens_out} · 工具 {row.tools}
                      {row.tool_names.length > 0 ? `（${row.tool_names.join('、')}）` : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 搜索 */}
        <div className="shrink-0 px-3 pb-2 pt-2.5">
          {/* 2026-11-17：图标从 absolute 定位搬进 ui/Input 的 leading 槽、清空按钮搬进 trailing 槽 ——
              正文让位（左 8 / 右 8 的内边距）由原语按尺寸档给，不再手写 padding-left / padding-right。 */}
          <Input
            type="text"
            size="sm"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索问话…"
            aria-label="搜索问话"
            leading={<Search size={12} className="text-slate-500" />}
            trailing={
              search ? (
                <Button type="button" onClick={() => setSearch('')} title="清空搜索" aria-label="清空搜索" variant="ghost" size="icon-xs">
                  <X size={11} />
                </Button>
              ) : undefined
            }
          />
        </div>

        {/* 列表 */}
        <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          {loading ? (
            <div className="flex flex-col items-center gap-2 pt-14 text-slate-500">
              <Loader2 size={16} className="animate-spin text-cyan-400/70" />
              <span className="text-body">加载中…</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-2 pt-14 text-slate-500">
              <p className="text-body text-rose-400/80">加载失败，请重试</p>
              <Button type="button" onClick={() => void loadItems()} variant="default" size="xs">
                <RefreshCw size={11} /> 重试
              </Button>
            </div>
          ) : rows.length === 0 ? (
            <div className="pt-14 text-center text-body text-slate-500">
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
                      className="group cursor-pointer rounded-lg border border-slate-800/80 bg-slate-900/40 px-2.5 py-2 outline-hidden transition-colors hover:border-cyan-500/35 hover:bg-slate-800/50 focus-visible:border-cyan-500/60"
                    >
                      <div className="flex items-start gap-2">
                        <p className="min-w-0 flex-1 whitespace-pre-wrap wrap-break-word text-body leading-relaxed text-slate-200 group-hover:text-slate-100">
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
                          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm text-slate-500 opacity-0 transition-colors group-hover:opacity-100 hover:bg-rose-500/10 hover:text-rose-400"
                        >
                          <Trash2 size={11} />
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between font-mono text-micro text-slate-600">
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
          <div className="shrink-0 border-t border-slate-800/80 px-4 py-2 text-caption text-amber-400/90">
            {hint}
          </div>
        )}
      </motion.aside>
    </div>
  );
}

export default HistoryDrawer;
