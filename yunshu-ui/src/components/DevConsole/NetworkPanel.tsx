/**
 * 网络请求面板
 *
 * 实时展示当前页所有 fetch/XHR 请求：URL、method、status、duration、trace_id
 *
 * 状态同步机制说明：数据来源于 requestInterceptor 的事件总线 → store → 本面板订阅，
 * 全程只读旁路采集，不修改任何业务请求语义。
 */

import React, { useState, useMemo, useCallback } from 'react';
import { useDevConsoleStore } from './store';
import type { NetworkRecord } from './types';
import {
  copyText,
  formatTime,
  formatDuration,
  statusBadgeClass,
  methodClass,
  truncate,
} from './shared';

const NetworkPanel: React.FC = () => {
  const records = useDevConsoleStore((s) => s.networkRecords);
  const filter = useDevConsoleStore((s) => s.networkFilter);
  const setFilter = useDevConsoleStore((s) => s.setNetworkFilter);
  const clearNetwork = useDevConsoleStore((s) => s.clearNetwork);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 按 URL 关键字过滤
  const filtered = useMemo(() => {
    if (!filter.trim()) return records;
    const kw = filter.trim().toLowerCase();
    return records.filter(
      (r) =>
        r.url.toLowerCase().includes(kw) ||
        (r.traceId && r.traceId.toLowerCase().includes(kw)) ||
        String(r.status).includes(kw)
    );
  }, [records, filter]);

  // 倒序展示（最新在顶部）
  const sorted = useMemo(
    () => [...filtered].reverse(),
    [filtered]
  );

  const handleCopyTrace = useCallback(
    async (e: React.MouseEvent, traceId: string | null) => {
      e.stopPropagation();
      if (!traceId) return;
      const ok = await copyText(traceId);
      if (!ok) console.warn('[DevConsole] 复制 trace_id 失败');
    },
    []
  );

  if (records.length === 0) {
    return <div className="devconsole-empty">暂无网络请求记录</div>;
  }

  return (
    <>
      <div className="devconsole-toolbar">
        <input
          className="devconsole-input"
          type="text"
          placeholder="过滤 URL / 状态码 / trace_id"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <button
          type="button"
          className="devconsole-btn danger"
          onClick={clearNetwork}
        >
          清空
        </button>
      </div>
      <div className="devconsole-body">
        <table className="devconsole-table">
          <thead>
            <tr>
              <th style={{ width: 60 }}>方法</th>
              <th>URL</th>
              <th style={{ width: 60 }}>状态</th>
              <th style={{ width: 70 }}>耗时</th>
              <th style={{ width: 120 }}>trace_id</th>
              <th style={{ width: 90 }}>时间</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <React.Fragment key={r.id}>
                <tr
                  className={`devconsole-row ${
                    expandedId === r.id ? 'expanded' : ''
                  }`}
                  onClick={() =>
                    setExpandedId(expandedId === r.id ? null : r.id)
                  }
                >
                  <td>
                    <span className={`devconsole-method ${methodClass(r.method)}`}>
                      {r.method}
                    </span>
                  </td>
                  <td title={r.url}>{truncate(r.url, 60)}</td>
                  <td>
                    <span className={`devconsole-badge ${statusBadgeClass(r.status)}`}>
                      {r.status === 0 ? 'ERR' : r.status}
                    </span>
                  </td>
                  <td>
                    <span className={`devconsole-badge ${r.status >= 400 || r.status === 0 ? 'badge-err' : 'badge-ok'}`}>
                      {formatDuration(r.duration)}
                    </span>
                  </td>
                  <td>
                    {r.traceId ? (
                      <span
                        className="devconsole-trace"
                        title={`点击复制: ${r.traceId}`}
                        onClick={(e) => handleCopyTrace(e, r.traceId)}
                      >
                        {truncate(r.traceId, 16)}
                      </span>
                    ) : (
                      <span style={{ color: '#484f58' }}>-</span>
                    )}
                  </td>
                  <td style={{ color: '#8b949e' }}>{formatTime(r.timestamp)}</td>
                </tr>
                {expandedId === r.id && (
                  <tr className="devconsole-row-detail">
                    <td colSpan={6}>
                      <NetworkRecordDetail record={r} />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
};

/** 单条记录详情展开 */
const NetworkRecordDetail: React.FC<{ record: NetworkRecord }> = ({
  record,
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopyAll = async () => {
    const text = JSON.stringify(record, null, 2);
    const ok = await copyText(text);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div>
      <div style={{ marginBottom: 6 }}>
        <strong style={{ color: '#7ee787' }}>完整 URL：</strong>
        <span style={{ userSelect: 'text', wordBreak: 'break-all' }}>
          {record.url}
        </span>
      </div>
      <div style={{ marginBottom: 6 }}>
        <strong style={{ color: '#7ee787' }}>来源：</strong>
        {record.source} | <strong style={{ color: '#7ee787' }}>耗时：</strong>
        {formatDuration(record.duration)} |{' '}
        <strong style={{ color: '#7ee787' }}>trace_id：</strong>
        {record.traceId || '无'}
      </div>
      {record.error && (
        <div style={{ marginBottom: 6 }}>
          <strong style={{ color: '#f85149' }}>错误：</strong>
          <span style={{ color: '#f85149' }}>{record.error}</span>
        </div>
      )}
      {record.requestBody && (
        <div style={{ marginBottom: 6 }}>
          <strong style={{ color: '#58a6ff' }}>请求体：</strong>
          <pre className="devconsole-code">{record.requestBody}</pre>
        </div>
      )}
      <button
        type="button"
        className="devconsole-btn"
        onClick={handleCopyAll}
      >
        {copied ? '已复制' : '复制完整记录'}
      </button>
    </div>
  );
};

export default NetworkPanel;
