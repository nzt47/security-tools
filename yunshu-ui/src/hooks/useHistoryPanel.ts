/**
 * 历史问话面板 hook — 加载/搜索/跳转/复制/删除
 *
 * 不变量【不易】：
 * - API 对接 /api/history（GET）和 /api/history/{index}（DELETE）
 * - 悬停触发：mouseenter 立即 open，mouseleave 300ms 后 close（与旧版对齐）
 * - 跳转：滚动到对应 user 消息 + 高亮 1.5s
 *
 * 简易【简易】：返回 { items, filtered, search, actions }
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { historyApi, type HistoryEntry } from '../lib/historyApi';

const AUTO_CLOSE_DELAY = 300;
const HIGHLIGHT_DURATION = 1500;

function formatHistTime(isoStr: string): string {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const h = pad(d.getHours());
    const mi = pad(d.getMinutes());
    const y = d.getFullYear();
    const m = pad(d.getMonth() + 1);
    const day = pad(d.getDate());
    const ny = now.getFullYear();
    const nm = pad(now.getMonth() + 1);
    const nday = pad(now.getDate());
    if (y === ny && m === nm && day === nday) return `${h}:${mi}`;
    return `${m}-${day} ${h}:${mi}`;
  } catch {
    return '';
  }
}

export function useHistoryPanel() {
  const [isOpen, setIsOpen] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [items, setItems] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const autoCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await historyApi.list();
      setItems(Array.isArray(data) ? data.reverse() : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const openPanel = useCallback(() => {
    if (autoCloseTimer.current) {
      clearTimeout(autoCloseTimer.current);
      autoCloseTimer.current = null;
    }
    if (!isOpen) {
      setIsOpen(true);
      setIsClosing(false);
      loadItems();
    }
  }, [isOpen, loadItems]);

  const closePanel = useCallback(() => {
    if (autoCloseTimer.current) clearTimeout(autoCloseTimer.current);
    setIsClosing(true);
    setTimeout(() => {
      setIsOpen(false);
      setIsClosing(false);
    }, 180);
  }, []);

  const cancelAutoClose = useCallback(() => {
    if (autoCloseTimer.current) {
      clearTimeout(autoCloseTimer.current);
      autoCloseTimer.current = null;
    }
  }, []);

  const scheduleAutoClose = useCallback(() => {
    if (autoCloseTimer.current) clearTimeout(autoCloseTimer.current);
    autoCloseTimer.current = setTimeout(() => {
      closePanel();
      autoCloseTimer.current = null;
    }, AUTO_CLOSE_DELAY);
  }, [closePanel]);

  const filtered = search
    ? items.filter((i) => (i.user || '').toLowerCase().includes(search.toLowerCase()))
    : items;

  const navigateTo = useCallback((msgIdx: number) => {
    const container = document.querySelector('.chat-messages');
    if (!container) return;
    const userMsgs = container.querySelectorAll<HTMLElement>('.message-group.user');
    const target = userMsgs[msgIdx];
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const originalBg = target.style.background;
      target.style.transition = 'background 0.3s';
      target.style.background = 'rgba(88,166,255,0.15)';
      setTimeout(() => {
        target.style.background = originalBg;
      }, HIGHLIGHT_DURATION);
      setActiveIdx(msgIdx);
    }
  }, []);

  const copyText = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // 降级方案
      const textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand('copy');
      } catch { /* 静默 */ }
      document.body.removeChild(textarea);
    }
  }, []);

  const deleteItem = useCallback(async (index: number) => {
    if (!window.confirm('确定要删除这条问话记录吗？')) return;
    try {
      await historyApi.delete(index);
      await loadItems();
    } catch (e) {
      console.error('[HistoryPanel] 删除失败:', e);
    }
  }, [loadItems]);

  useEffect(() => {
    return () => {
      if (autoCloseTimer.current) clearTimeout(autoCloseTimer.current);
    };
  }, []);

  return {
    isOpen,
    isClosing,
    items,
    filtered,
    loading,
    error,
    search,
    activeIdx,
    formatHistTime,
    actions: {
      openPanel,
      closePanel,
      cancelAutoClose,
      scheduleAutoClose,
      setSearch,
      navigateTo,
      copyText,
      deleteItem,
    },
  };
}
