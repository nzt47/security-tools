/**
 * useObservableState —— 业务组件 opt-in 接入 StateInspector 的 Hook
 *
 * 用法：
 * ```tsx
 * const [value, setValue] = useObservableState('chatInput', '', {
 *   source: 'memory',
 *   traceId: currentTraceId,
 * });
 * ```
 *
 * 状态同步机制说明：
 * - 内部用 useState 持有真实状态，保证业务组件渲染语义不变；
 * - setValue 时同步上报快照到 StateInspector store（旁路采集，不影响业务）；
 * - 支持函数式更新：setValue(prev => ...) 与 React useState 一致。
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { isObservabilityEnabled } from '@/config/observability';
import { useStateInspectorStore } from '../store';
import type {
  UseObservableStateOptions,
  ObservableSetter,
} from '../types';

export function useObservableState<T>(
  key: string,
  initialValue: T,
  options: UseObservableStateOptions<T> = {}
): [T, ObservableSetter<T>] {
  const [value, setValue] = useState<T>(initialValue);

  // 保持最新 options 的引用，避免 upsertSnapshot 依赖变化导致重注册
  const optionsRef = useRef(options);
  optionsRef.current = options;

  // 初始化：注册初始快照（仅 dev 环境）
  useEffect(() => {
    if (!isObservabilityEnabled()) return;
    const opts = optionsRef.current;
    useStateInspectorStore.getState().upsertSnapshot(key, initialValue, {
      expiresAt: opts.expiresAt,
      retryCount: opts.retryCount,
      traceId: opts.traceId,
      source: opts.source,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // 包装 setter：更新本地状态 + 上报快照
  const setter = useCallback<ObservableSetter<T>>(
    (next) => {
      setValue((prev) => {
        const resolved =
          typeof next === 'function' ? (next as (p: T) => T)(prev) : next;
        // 旁路上报（不阻塞业务）
        if (isObservabilityEnabled()) {
          const opts = optionsRef.current;
          try {
            useStateInspectorStore.getState().upsertSnapshot(key, resolved, {
              expiresAt: opts.expiresAt,
              retryCount: opts.retryCount,
              traceId: opts.traceId,
              source: opts.source,
            });
          } catch (err) {
            // 上报失败不影响业务
            console.error('[StateInspector] 上报快照失败:', err);
          }
        }
        return resolved;
      });
    },
    [key]
  );

  return [value, setter];
}
