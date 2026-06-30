/**
 * 性能面板
 *
 * 展示组件渲染耗时（通过 trackPerf / measureRender 上报）。
 * 耗时分级：< 16ms（绿，满足 60fps）/ 16-100ms（黄）/ > 100ms（红）。
 */

import React, { useMemo } from 'react';
import { useDevConsoleStore } from './store';
import { formatTime, formatDuration, durationClass } from './shared';

const PerformancePanel: React.FC = () => {
  const records = useDevConsoleStore((s) => s.perfRecords);
  const clearPerf = useDevConsoleStore((s) => s.clearPerf);

  const sorted = useMemo(
    () => [...records].reverse(),
    [records]
  );

  // 统计：平均耗时 / 最大耗时
  const stats = useMemo(() => {
    if (records.length === 0) return { avg: 0, max: 0, count: 0 };
    const sum = records.reduce((acc, r) => acc + r.duration, 0);
    const max = records.reduce((m, r) => Math.max(m, r.duration), 0);
    return { avg: sum / records.length, max, count: records.length };
  }, [records]);

  if (records.length === 0) {
    return (
      <div className="devconsole-empty">
        暂无性能记录
        <div style={{ marginTop: 8, fontSize: 11, color: '#484f58' }}>
          使用 trackPerf() 或 measureRender() 上报渲染耗时
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="devconsole-toolbar">
        <span style={{ color: '#8b949e', fontSize: 11 }}>
          共 {stats.count} 条 | 平均 {formatDuration(stats.avg)} | 最大{' '}
          {formatDuration(stats.max)}
        </span>
        <button
          type="button"
          className="devconsole-btn danger"
          onClick={clearPerf}
        >
          清空
        </button>
      </div>
      <div className="devconsole-body">
        <table className="devconsole-table">
          <thead>
            <tr>
              <th>指标名称</th>
              <th style={{ width: 90 }}>耗时</th>
              <th style={{ width: 90 }}>时间</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.id}>
                <td style={{ color: '#58a6ff' }}>{r.name}</td>
                <td>
                  <span className={`devconsole-badge ${durationClass(r.duration)}`}>
                    {formatDuration(r.duration)}
                  </span>
                </td>
                <td style={{ color: '#8b949e' }}>{formatTime(r.timestamp)}</td>
                <td style={{ color: '#8b949e', fontSize: 11 }}>
                  {r.detail ? JSON.stringify(r.detail) : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
};

export default PerformancePanel;
