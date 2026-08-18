/**
 * StateInspector 数据存储
 *
 * 管理状态快照（Map）与状态变更时间线（timeline）。
 *
 * 状态同步机制说明：
 * - 快照采用 Map 不可变更新，保证 React 重渲染；
 * - diff 计算在 setValue 时同步完成，时间线条目按 LRU 保留最近 maxRecords 条；
 * - 倒计时不在 store 中计算（避免高频 set），由组件端用 setInterval 派生展示。
 */

import { create } from 'zustand';
import { getObservabilityConfig, isObservabilityEnabled } from '@/config/observability';
import type {
  StateSnapshot,
  StateDiff,
  StateTimelineEntry,
} from './types';

/** 容量上限 */
const MAX_TIMELINE = isObservabilityEnabled()
  ? getObservabilityConfig().maxRecords
  : 200;

/** 生成唯一 ID */
function genId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 计算两个值的 diff（浅层 + 一层对象字段比较）
 *
 * 性能优先：仅做一层对象/数组比较，深嵌套结构整体标记 updated。
 */
export function diffValues(oldVal: unknown, newVal: unknown): StateDiff[] {
  // 基本类型直接比较
  if (oldVal === newVal) return [];

  // 类型不同 → 整体更新
  if (
    typeof oldVal !== typeof newVal ||
    oldVal === null ||
    newVal === null ||
    typeof oldVal !== 'object' ||
    typeof newVal !== 'object'
  ) {
    return [{ path: '', oldValue: oldVal, newValue: newVal, kind: 'updated' }];
  }

  const diffs: StateDiff[] = [];
  const oldObj = oldVal as Record<string, unknown>;
  const newObj = newVal as Record<string, unknown>;

  // 数组用 JSON 整体比较（避免索引 diff 复杂度）
  if (Array.isArray(oldVal) && Array.isArray(newVal)) {
    if (JSON.stringify(oldVal) !== JSON.stringify(newVal)) {
      return [{ path: '', oldValue: oldVal, newValue: newVal, kind: 'updated' }];
    }
    return [];
  }

  // 新增字段
  for (const key of Object.keys(newObj)) {
    if (!(key in oldObj)) {
      diffs.push({
        path: key,
        oldValue: undefined,
        newValue: newObj[key],
        kind: 'added',
      });
    } else if (oldObj[key] !== newObj[key]) {
      diffs.push({
        path: key,
        oldValue: oldObj[key],
        newValue: newObj[key],
        kind: 'updated',
      });
    }
  }
  // 删除字段
  for (const key of Object.keys(oldObj)) {
    if (!(key in newObj)) {
      diffs.push({
        path: key,
        oldValue: oldObj[key],
        newValue: undefined,
        kind: 'removed',
      });
    }
  }

  return diffs;
}

interface StateInspectorState {
  /** 状态快照（key -> snapshot） */
  snapshots: Map<string, StateSnapshot>;
  /** 状态变更时间线（按时间正序，尾部为最新） */
  timeline: StateTimelineEntry[];

  /** 注册/更新一个状态快照 */
  upsertSnapshot: (
    key: string,
    value: unknown,
    opts?: {
      expiresAt?: number;
      retryCount?: number;
      traceId?: string | null;
      source?: 'localStorage' | 'sessionStorage' | 'memory';
    }
  ) => void;
  /** 增加重试次数（对齐"重试次数可见"要求） */
  incrementRetry: (key: string, traceId?: string | null) => void;
  /** 清空时间线 */
  clearTimeline: () => void;
  /** 清空全部 */
  clearAll: () => void;
}

export const useStateInspectorStore = create<StateInspectorState>(
  (set, get) => ({
    snapshots: new Map(),
    timeline: [],

    upsertSnapshot: (key, value, opts = {}) => {
      const now = Date.now();
      set((state) => {
        const prevSnap = state.snapshots.get(key);
        const oldValue = prevSnap?.value;

        // 更新快照 Map（不可变）
        const newMap = new Map(state.snapshots);
        newMap.set(key, {
          key,
          value,
          updatedAt: now,
          expiresAt: opts.expiresAt ?? prevSnap?.expiresAt ?? 0,
          retryCount: opts.retryCount ?? prevSnap?.retryCount ?? 0,
          traceId: opts.traceId ?? prevSnap?.traceId ?? null,
          source: opts.source ?? prevSnap?.source ?? 'memory',
        });

        // 计算 diff 并追加时间线
        let newTimeline = state.timeline;
        if (prevSnap && oldValue !== value) {
          const diffs = diffValues(oldValue, value);
          if (diffs.length > 0) {
            const entry: StateTimelineEntry = {
              id: genId(),
              key,
              timestamp: now,
              diffs,
              traceId: opts.traceId ?? prevSnap.traceId,
            };
            newTimeline = [...state.timeline, entry];
            // LRU 淘汰
            if (newTimeline.length > MAX_TIMELINE) {
              newTimeline = newTimeline.slice(
                newTimeline.length - MAX_TIMELINE
              );
            }
          }
        }

        return { snapshots: newMap, timeline: newTimeline };
      });
    },

    incrementRetry: (key, traceId = null) => {
      set((state) => {
        const prev = state.snapshots.get(key);
        if (!prev) return state;
        const newMap = new Map(state.snapshots);
        newMap.set(key, {
          ...prev,
          retryCount: prev.retryCount + 1,
          updatedAt: Date.now(),
          traceId: traceId ?? prev.traceId,
        });
        return { snapshots: newMap };
      });
    },

    clearTimeline: () => set({ timeline: [] }),
    clearAll: () => set({ snapshots: new Map(), timeline: [] }),
  })
);
