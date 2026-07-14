/**
 * 上下文监视器 hook — 轮询 /api/context/status + 控制方法
 *
 * 不变量【不易】：
 * - 轮询间隔 5s（与旧版 _ctxPollTimer 对齐）
 * - expanded/helpOpen 持久化 localStorage:yunshu_ctx_monitor
 * - saveConfig 节流 300ms（与旧版 _ctxDebounceTimer 对齐）
 * - AbortError 静默
 *
 * 简易【简易】：返回 { status, loading, expanded, helpOpen, notice, actions }
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { usePolling } from './usePolling';
import {
  contextMonitorApi,
  type ContextStatus,
  type ContextConfig,
} from '../lib/contextMonitorApi';

const POLL_INTERVAL = 5000;
const STORAGE_KEY = 'yunshu_ctx_monitor';

interface PersistedState {
  expanded?: boolean;
  helpOpen?: boolean;
}

function loadPersisted(): PersistedState {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function savePersisted(state: PersistedState) {
  try {
    const saved = loadPersisted();
    Object.assign(saved, state);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  } catch { /* 静默 */ }
}

export interface ContextNotice {
  text: string;
  color: string;
  id: number;
}

export function useContextMonitor() {
  const persisted = useRef(loadPersisted());
  const [expanded, setExpanded] = useState<boolean>(persisted.current.expanded || false);
  const [helpOpen, setHelpOpen] = useState<boolean>(persisted.current.helpOpen || false);
  const [notice, setNotice] = useState<ContextNotice | null>(null);
  const [saving, setSaving] = useState(false);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data: status, error } = usePolling<ContextStatus>(
    (signal) => contextMonitorApi.status(signal),
    POLL_INTERVAL,
  );

  const showNotice = useCallback((text: string, color: string) => {
    setNotice({ text, color, id: Date.now() });
    setTimeout(() => setNotice(null), 3000);
  }, []);

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      savePersisted({ expanded: next });
      return next;
    });
  }, []);

  const toggleHelp = useCallback(() => {
    setHelpOpen((prev) => {
      const next = !prev;
      savePersisted({ helpOpen: next });
      return next;
    });
  }, []);

  const saveConfig = useCallback((config: ContextConfig) => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(async () => {
      setSaving(true);
      try {
        await contextMonitorApi.saveConfig(config);
      } catch { /* 静默 */ } finally {
        setSaving(false);
      }
    }, 300);
  }, []);

  const compress = useCallback(async () => {
    try {
      const result = await contextMonitorApi.compress();
      if (result.ok) {
        showNotice(`✅ 压缩完成，释放 ${result.freed_tokens || 0} tokens`, '#3fb950');
      }
    } catch { /* 静默 */ }
  }, [showNotice]);

  const resetStats = useCallback(() => {
    showNotice('📊 统计已刷新', '#8b949e');
  }, [showNotice]);

  useEffect(() => {
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, []);

  return {
    status: status ?? null,
    loading: !status && !error,
    error,
    expanded,
    helpOpen,
    notice,
    saving,
    actions: {
      toggleExpanded,
      toggleHelp,
      saveConfig,
      compress,
      resetStats,
      showNotice,
    },
  };
}
