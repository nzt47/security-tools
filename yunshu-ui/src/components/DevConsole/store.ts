/**
 * DevConsole 数据存储
 *
 * 使用 zustand 管理网络/错误/性能三类记录，配合 LRU 容量淘汰与暂停/过滤能力。
 *
 * 状态同步机制说明：
 * - LRU 淘汰：记录超出 maxRecords 时按 FIFO 移除最旧条目，保证内存占用有上限；
 * - 拦截器订阅：install() 通过 subscribeNetwork/subscribeError 订阅事件总线，
 *   返回卸载函数，组件卸载时自动取消订阅，避免内存泄漏。
 */

import { create } from 'zustand';
import {
  getObservabilityConfig,
  isObservabilityEnabled,
} from '@/config/observability';
import {
  subscribeNetwork,
  subscribeError,
  installInterceptors,
} from '@/utils/requestInterceptor';
import type {
  NetworkRecord,
  ErrorRecord,
  PerfRecord,
} from './types';

/** 生成唯一 ID */
function genId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

interface DevConsoleState {
  /** 网络请求记录（按时间正序，尾部为最新） */
  networkRecords: NetworkRecord[];
  /** 错误记录 */
  errorRecords: ErrorRecord[];
  /** 性能记录 */
  perfRecords: PerfRecord[];
  /** 是否暂停采集（暂停期间丢弃新记录） */
  paused: boolean;
  /** 网络面板 URL 过滤关键字 */
  networkFilter: string;

  // ─── actions ───
  addNetworkRecord: (r: NetworkRecord) => void;
  addErrorRecord: (r: ErrorRecord) => void;
  addPerfRecord: (r: PerfRecord) => void;
  clearNetwork: () => void;
  clearError: () => void;
  clearPerf: () => void;
  clearAll: () => void;
  togglePause: () => void;
  setNetworkFilter: (keyword: string) => void;
  /** 安装拦截器并订阅事件，返回卸载函数 */
  install: () => () => void;
}

/** 容量上限（来自可观测性配置） */
const MAX_RECORDS = isObservabilityEnabled()
  ? getObservabilityConfig().maxRecords
  : 200;

/**
 * 追加记录并执行 LRU 淘汰
 *
 * @param list 现有列表
 * @param item 新记录
 * @param max 最大容量
 * @returns 裁剪后的新列表
 */
function appendWithLru<T>(list: T[], item: T, max: number): T[] {
  const next = list.length >= max ? list.slice(list.length - max + 1) : list;
  next.push(item);
  return next;
}

export const useDevConsoleStore = create<DevConsoleState>((set, get) => ({
  networkRecords: [],
  errorRecords: [],
  perfRecords: [],
  paused: false,
  networkFilter: '',

  addNetworkRecord: (r) => {
    if (get().paused) return; // 暂停期间丢弃
    set((state) => ({
      networkRecords: appendWithLru(state.networkRecords, r, MAX_RECORDS),
    }));
  },

  addErrorRecord: (r) => {
    if (get().paused) return;
    set((state) => ({
      errorRecords: appendWithLru(state.errorRecords, r, MAX_RECORDS),
    }));
  },

  addPerfRecord: (r) => {
    if (get().paused) return;
    set((state) => ({
      perfRecords: appendWithLru(state.perfRecords, r, MAX_RECORDS),
    }));
  },

  clearNetwork: () => set({ networkRecords: [] }),
  clearError: () => set({ errorRecords: [] }),
  clearPerf: () => set({ perfRecords: [] }),
  clearAll: () =>
    set({ networkRecords: [], errorRecords: [], perfRecords: [] }),

  togglePause: () => set((state) => ({ paused: !state.paused })),
  setNetworkFilter: (keyword) => set({ networkFilter: keyword }),

  install: () => {
    // 环境守卫：生产环境直接返回空卸载函数
    if (!isObservabilityEnabled()) return () => {};

    // 订阅事件总线
    const unsubNetwork = subscribeNetwork((r) => {
      get().addNetworkRecord(r);
    });
    const unsubError = subscribeError((r) => {
      get().addErrorRecord(r);
    });

    // 安装底层拦截器（fetch / XHR / error）
    const uninstallInterceptor = installInterceptors();

    // 返回统一卸载函数
    return () => {
      unsubNetwork();
      unsubError();
      uninstallInterceptor();
    };
  },
}));

// ─── 性能采集工具 ──────────────────────────────────────────────────────

/**
 * 记录一条性能指标
 *
 * 单次埋点耗时 < 1ms，埋点失败不影响主流程（吞掉异常，仅日志记录）。
 *
 * @param name 指标名称
 * @param duration 耗时（毫秒）
 * @param detail 附加详情
 */
export function trackPerf(
  name: string,
  duration: number,
  detail?: Record<string, unknown>
): void {
  try {
    if (!isObservabilityEnabled()) return;
    useDevConsoleStore.getState().addPerfRecord({
      id: genId(),
      name,
      duration,
      timestamp: Date.now(),
      detail,
    });
  } catch (err) {
    // 埋点失败不影响主业务流程
    console.error('[Observability] trackPerf 失败:', err);
  }
}

/**
 * 测量同步函数执行耗时并记录
 *
 * @param name 指标名称
 * @param fn 待测量函数
 * @returns fn 的返回值
 */
export function measureRender<T>(name: string, fn: () => T): T {
  if (!isObservabilityEnabled()) return fn();
  const start = performance.now();
  try {
    return fn();
  } finally {
    const duration = performance.now() - start;
    trackPerf(name, duration);
  }
}
