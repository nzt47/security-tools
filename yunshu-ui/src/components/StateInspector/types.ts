/**
 * StateInspector 类型定义
 *
 * StateSnapshot / StateDiff / StateTimelineEntry 复用自 DevConsole/types.ts，
 * 本文件仅定义 StateInspector 专有类型。
 */

export type {
  StateSnapshot,
  StateDiff,
  StateTimelineEntry,
} from '@/components/DevConsole/types';

/** useObservableState Hook 配置项 */
export interface UseObservableStateOptions<T> {
  /** 过期时间戳（毫秒），0 表示不过期。对齐文章"staleTime 倒计时"要求 */
  expiresAt?: number;
  /** 重试次数，对齐文章"重试次数可见"要求 */
  retryCount?: number;
  /** 关联 trace_id */
  traceId?: string | null;
  /** 状态来源 */
  source?: 'localStorage' | 'sessionStorage' | 'memory';
  /** 显示标签（默认用 key） */
  label?: string;
  /** 是否启用 diff 记录（默认 true，高频更新场景可关闭以节省性能） */
  enableDiff?: boolean;
}

/** useObservableState 返回值 */
export type ObservableSetter<T> = (value: T | ((prev: T) => T)) => void;
