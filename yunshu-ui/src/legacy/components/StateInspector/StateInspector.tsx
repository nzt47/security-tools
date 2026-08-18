/**
 * StateInspector 状态调试面板
 *
 * 展示：
 * 1. 状态快照实时列表（localStorage / sessionStorage / memory）
 * 2. 缓存过期倒计时（对齐文章"看到 staleTime 倒计时归零并触发重新获取"要求）
 * 3. 请求队列重试次数（对齐"重试次数可见"要求）
 * 4. 状态变化时间线（diff 视图，高亮变更字段）
 *
 * 状态同步机制说明：面板数据来自 useStateInspectorStore，与 useObservableState
 * 写入同一 store，保证"所见即所得"；倒计时用 setInterval 派生展示，不污染 store。
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useDevConsoleStore } from '@/components/DevConsole/store';
import { useStateInspectorStore } from './store';
import { copyText, formatTime, formatDuration } from '@/components/DevConsole/shared';
import { trackEvent, TrackEventName } from '@/config/observability';
import type { StateSnapshot, StateTimelineEntry, StateDiff } from './types';
import '../DevConsole/DevConsole.css';

const StateInspector: React.FC = () => {
  const snapshots = useStateInspectorStore((s) => s.snapshots);
  const timeline = useStateInspectorStore((s) => s.timeline);
  const clearTimeline = useStateInspectorStore((s) => s.clearTimeline);
  const clearAll = useStateInspectorStore((s) => s.clearAll);
  const [activeView, setActiveView] = useState<'snapshots' | 'timeline'>(
    'snapshots'
  );

  // 每秒刷新倒计时（派生展示，不写 store）
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!snapshots.size) return;
    const timer = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(timer);
  }, [snapshots.size]);

  const snapList = useMemo(
    () => Array.from(snapshots.values()),
    [snapshots]
  );
  const sortedTimeline = useMemo(
    () => [...timeline].reverse(),
    [timeline]
  );

  return (
    <>
      <div className="devconsole-toolbar">
        <button
          type="button"
          className={`devconsole-btn ${activeView === 'snapshots' ? '' : 'warning'}`}
          onClick={() => {
            setActiveView('snapshots');
            // 埋点：快照视图切换（D5，filter_apply）
            trackEvent(TrackEventName.FILTER_APPLY, {
              module: 'state_inspector',
              success: true,
              filters: 'snapshots',
            });
          }}
        >
          快照（{snapList.length}）
        </button>
        <button
          type="button"
          className={`devconsole-btn ${activeView === 'timeline' ? '' : 'warning'}`}
          onClick={() => {
            setActiveView('timeline');
            // 埋点：时间线视图切换（D5，filter_apply）
            trackEvent(TrackEventName.FILTER_APPLY, {
              module: 'state_inspector',
              success: true,
              filters: 'timeline',
            });
          }}
        >
          时间线（{timeline.length}）
        </button>
        <button
          type="button"
          className="devconsole-btn danger"
          onClick={() => {
            clearAll();
            // 埋点：清空全部（D5，form_submit）
            trackEvent(TrackEventName.FORM_SUBMIT, {
              module: 'state_inspector_clear',
              success: true,
            });
          }}
          style={{ marginLeft: 'auto' }}
        >
          清空全部
        </button>
      </div>

      <div className="devconsole-body">
        {activeView === 'snapshots' ? (
          <SnapshotsView snapshots={snapList} />
        ) : (
          <TimelineView timeline={sortedTimeline} onClear={clearTimeline} />
        )}
      </div>
    </>
  );
};

// ─── 快照视图 ──────────────────────────────────────────────────────────

const SnapshotsView: React.FC<{ snapshots: StateSnapshot[] }> = ({
  snapshots,
}) => {
  if (snapshots.length === 0) {
    return (
      <div className="devconsole-empty">
        暂无状态快照
        <div style={{ marginTop: 8, fontSize: 11, color: '#484f58' }}>
          使用 useObservableState() 接入业务状态
        </div>
      </div>
    );
  }

  const now = Date.now();

  return (
    <div>
      {snapshots.map((snap) => {
        const remaining = snap.expiresAt > 0 ? snap.expiresAt - now : -1;
        const isExpired = snap.expiresAt > 0 && remaining <= 0;
        const sourceBadge =
          snap.source === 'localStorage'
            ? 'badge-info'
            : snap.source === 'sessionStorage'
            ? 'badge-warn'
            : 'badge-muted';

        return (
          <div key={snap.key} className="stateinspector-section">
            <div style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong style={{ color: '#58a6ff' }}>{snap.key}</strong>
              <span className={`devconsole-badge ${sourceBadge}`}>
                {snap.source}
              </span>
              {snap.retryCount > 0 && (
                <span className="devconsole-badge badge-err">
                  重试 {snap.retryCount} 次
                </span>
              )}
            </div>
            <div className="stateinspector-kv">
              <span className="stateinspector-key">value</span>
              <span className="stateinspector-value">
                {formatValue(snap.value)}
              </span>
            </div>
            <div className="stateinspector-kv">
              <span className="stateinspector-key">updatedAt</span>
              <span className="stateinspector-value">
                {formatTime(snap.updatedAt)}
              </span>
            </div>
            {snap.expiresAt > 0 && (
              <div className="stateinspector-kv">
                <span className="stateinspector-key">staleTime</span>
                <span
                  className={`stateinspector-countdown ${isExpired ? 'expired' : ''}`}
                >
                  {isExpired
                    ? '已过期（将触发重新获取）'
                    : `剩余 ${formatDuration(remaining)}`}
                </span>
              </div>
            )}
            {snap.traceId && (
              <div className="stateinspector-kv">
                <span className="stateinspector-key">trace_id</span>
                <span
                  className="stateinspector-value devconsole-trace"
                  title="点击复制"
                  onClick={async (e) => {
                    e.stopPropagation();
                    await copyText(snap.traceId!);
                  }}
                >
                  {snap.traceId}
                </span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

// ─── 时间线视图 ────────────────────────────────────────────────────────

const TimelineView: React.FC<{
  timeline: StateTimelineEntry[];
  onClear: () => void;
}> = ({ timeline, onClear }) => {
  if (timeline.length === 0) {
    return (
      <div className="devconsole-empty">
        暂无状态变更记录
        <div style={{ marginTop: 8, fontSize: 11, color: '#484f58' }}>
          状态变化时会自动记录 diff
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ padding: '4px 12px', textAlign: 'right' }}>
        <button
          type="button"
          className="devconsole-btn danger"
          onClick={onClear}
        >
          清空时间线
        </button>
      </div>
      {timeline.map((entry) => (
        <div key={entry.id} className="stateinspector-section">
          <div style={{ marginBottom: 4 }}>
            <strong style={{ color: '#58a6ff' }}>{entry.key}</strong>
            <span style={{ color: '#8b949e', marginLeft: 8 }}>
              {formatTime(entry.timestamp)}
            </span>
            {entry.traceId && (
              <span style={{ color: '#8b949e', marginLeft: 8, fontSize: 11 }}>
                trace: {entry.traceId.slice(0, 12)}…
              </span>
            )}
          </div>
          {entry.diffs.map((diff, i) => (
            <DiffRow key={i} diff={diff} />
          ))}
        </div>
      ))}
    </div>
  );
};

const DiffRow: React.FC<{ diff: StateDiff }> = ({ diff }) => {
  const cls =
    diff.kind === 'added'
      ? 'stateinspector-diff-added'
      : diff.kind === 'removed'
      ? 'stateinspector-diff-removed'
      : 'stateinspector-diff-updated';
  const label =
    diff.kind === 'added'
      ? '+ 新增'
      : diff.kind === 'removed'
      ? '- 删除'
      : '~ 更新';

  return (
    <div className={`stateinspector-kv ${cls}`} style={{ borderRadius: 3 }}>
      <span className="stateinspector-key">
        {label} {diff.path || '(root)'}
      </span>
      <span className="stateinspector-value">
        {diff.kind === 'added' ? (
          <>{formatValue(diff.newValue)}</>
        ) : diff.kind === 'removed' ? (
          <>{formatValue(diff.oldValue)}</>
        ) : (
          <>
            {formatValue(diff.oldValue)} → {formatValue(diff.newValue)}
          </>
        )}
      </span>
    </div>
  );
};

/** 格式化任意值为可读字符串 */
function formatValue(val: unknown): string {
  if (val === undefined) return 'undefined';
  if (val === null) return 'null';
  if (typeof val === 'string') return val.length > 100 ? `${val.slice(0, 100)}…` : val;
  if (typeof val === 'object') {
    try {
      const s = JSON.stringify(val);
      return s.length > 100 ? `${s.slice(0, 100)}…` : s;
    } catch {
      return '[object]';
    }
  }
  return String(val);
}

export default StateInspector;
