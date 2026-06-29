/**
 * 错误堆栈面板
 *
 * 捕获未处理异常（window.onerror）与 Promise rejection（unhandledrejection），
 * 展示堆栈与对应 trace_id。
 */

import React, { useState, useMemo, useCallback } from 'react';
import { useDevConsoleStore } from './store';
import { copyText, formatTime, truncate } from './shared';

const ErrorPanel: React.FC = () => {
  const records = useDevConsoleStore((s) => s.errorRecords);
  const clearError = useDevConsoleStore((s) => s.clearError);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const sorted = useMemo(
    () => [...records].reverse(),
    [records]
  );

  const handleCopyTrace = useCallback(
    async (e: React.MouseEvent, traceId: string | null) => {
      e.stopPropagation();
      if (!traceId) return;
      await copyText(traceId);
    },
    []
  );

  if (records.length === 0) {
    return <div className="devconsole-empty">暂无错误记录</div>;
  }

  return (
    <>
      <div className="devconsole-toolbar">
        <span style={{ color: '#8b949e', fontSize: 11 }}>
          共 {records.length} 条错误
        </span>
        <button
          type="button"
          className="devconsole-btn danger"
          onClick={clearError}
        >
          清空
        </button>
      </div>
      <div className="devconsole-body">
        <table className="devconsole-table">
          <thead>
            <tr>
              <th style={{ width: 130 }}>类型</th>
              <th>消息</th>
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
                    <span
                      className={`devconsole-badge ${
                        r.type === 'onerror' ? 'badge-err' : 'badge-warn'
                      }`}
                    >
                      {r.type === 'onerror' ? '异常' : 'Promise'}
                    </span>
                  </td>
                  <td title={r.message}>{truncate(r.message, 70)}</td>
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
                    <td colSpan={4}>
                      <ErrorRecordDetail record={r} />
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

const ErrorRecordDetail: React.FC<{
  record: import('./types').ErrorRecord;
}> = ({ record }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const text = `${record.message}\n\n${record.stack}`;
    const ok = await copyText(text);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div>
      <div style={{ marginBottom: 6 }}>
        <strong style={{ color: '#f85149' }}>消息：</strong>
        <span style={{ userSelect: 'text' }}>{record.message}</span>
      </div>
      {record.source && (
        <div style={{ marginBottom: 6 }}>
          <strong style={{ color: '#7ee787' }}>来源：</strong>
          <span style={{ userSelect: 'text' }}>
            {record.source}
          </span>
          {record.lineno !== undefined && (
            <span> : {record.lineno}{record.colno !== undefined ? `:${record.colno}` : ''}</span>
          )}
        </div>
      )}
      {record.traceId && (
        <div style={{ marginBottom: 6 }}>
          <strong style={{ color: '#58a6ff' }}>trace_id：</strong>
          <span style={{ userSelect: 'text' }}>{record.traceId}</span>
        </div>
      )}
      {record.stack && (
        <div style={{ marginBottom: 6 }}>
          <strong style={{ color: '#7ee787' }}>堆栈：</strong>
          <pre className="devconsole-code">{record.stack}</pre>
        </div>
      )}
      <button
        type="button"
        className="devconsole-btn"
        onClick={handleCopy}
      >
        {copied ? '已复制' : '复制堆栈'}
      </button>
    </div>
  );
};

export default ErrorPanel;
