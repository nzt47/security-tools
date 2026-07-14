/**
 * ContextMonitor — 上下文监视器
 *
 * 从旧版 templates/index.html L626-770 + static/css/context-monitor.css 移植。
 * 折叠状态：进度条 + 百分比 + 详情 + 控制按钮
 * 展开状态：状态概览 + 用量统计 + 控制面板（滑块）+ 机制说明 + 最近消息
 *
 * 不变量【不易】：
 * - status 字段名与后端 /api/context/status 严格对齐
 * - 滑块拖动时不被 status 轮询覆盖（聚焦判断）
 * - expanded/helpOpen 持久化 localStorage:yunshu_ctx_monitor
 */
import React, { useEffect, useRef, useState } from 'react';
import { useContextMonitor } from '../../hooks/useContextMonitor';
import './ContextMonitor.css';

const STATUS_MAP: Record<string, { dot: string; text: string }> = {
  ok: { dot: '🟢', text: '正常' },
  info: { dot: '🔵', text: '接近阈值' },
  warning: { dot: '🟡', text: '接近阈值' },
  critical: { dot: '🔴', text: '即将溢出' },
};

const fmtNum = (n: number): string => (n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n));

function getLevel(pct: number): 'safe' | 'warn' | 'danger' {
  if (pct >= 80) return 'danger';
  if (pct >= 60) return 'warn';
  return 'safe';
}

interface SliderRowProps {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  hint: string;
  onChange: (v: number) => void;
}

function SliderRow({ label, min, max, step, value, hint, onChange }: SliderRowProps) {
  const [localValue, setLocalValue] = useState(value);
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!focused && inputRef.current && !inputRef.current.matches(':focus')) {
      setLocalValue(value);
    }
  }, [value, focused]);

  return (
    <>
      <div className="ctx-slider-row">
        <label>{label}</label>
        <input
          ref={inputRef}
          type="range"
          min={min}
          max={max}
          step={step}
          value={localValue}
          onChange={(e) => {
            setLocalValue(Number(e.target.value));
            onChange(Number(e.target.value));
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <span className="ctx-slider-val">{localValue}</span>
      </div>
      <div className="ctx-hint ctx-hint-slider">{hint}</div>
    </>
  );
}

export const ContextMonitor: React.FC = () => {
  const { status, expanded, helpOpen, notice, saving, actions } = useContextMonitor();

  if (!status) {
    return (
      <div className="ctx-bar ctx-collapsed">
        <div className="ctx-bar-collapsed">
          <span className="ctx-icon">📊</span>
          <span className="ctx-label">上下文</span>
          <span className="ctx-detail">加载中...</span>
        </div>
      </div>
    );
  }

  const pct = status.percentage || 0;
  const total = status.current_tokens || 0;
  const limit = status.token_limit || 4096;
  const rounds = status.compress_rounds || 0;
  const statusLevel = status.status_level || 'ok';
  const level = getLevel(pct);
  const st = STATUS_MAP[statusLevel] || STATUS_MAP.ok;
  const fmtPct = pct.toFixed(0);

  const sendLimit = status.per_message_send_limit || 2048;
  const recvLimit = status.per_message_recv_limit || 4096;

  const recent = status.recent_messages || [];

  const handleSaveConfig = (tokenLimit?: number, sendLmt?: number, recvLmt?: number) => {
    actions.saveConfig({
      token_limit: tokenLimit,
      per_message_send_limit: sendLmt,
      per_message_recv_limit: recvLmt,
    });
  };

  return (
    <div className={`ctx-bar ${expanded ? 'ctx-expanded' : 'ctx-collapsed'}`}>
      {/* 折叠状态 */}
      <div className="ctx-bar-collapsed" onClick={actions.toggleExpanded} title="点击展开上下文监视器">
        <span className="ctx-icon">📊</span>
        <span className="ctx-label">上下文</span>
        <div className="ctx-progress-mini">
          <div
            className={`ctx-progress-fill ${level === 'danger' ? 'ctx-danger' : level === 'warn' ? 'ctx-warn' : ''}`}
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <span className="ctx-percent">{fmtPct}%</span>
        <span className="ctx-detail">{fmtNum(total)}/{fmtNum(limit)}</span>
        <span className="ctx-spacer" />
        <span className="ctx-btn" onClick={(e) => { e.stopPropagation(); actions.toggleExpanded(); }} title="展开/折叠">
          {expanded ? '−' : '+'}
        </span>
        <span className="ctx-btn" onClick={(e) => { e.stopPropagation(); actions.toggleExpanded(); }} title="展开">□</span>
        <span className="ctx-btn" onClick={(e) => { e.stopPropagation(); actions.toggleExpanded(); }} title="折叠">×</span>
      </div>

      {/* 展开状态 */}
      {expanded && (
        <div className="ctx-bar-expanded">
          <div className="ctx-header">
            <span>📊 上下文监视器</span>
            <div className="ctx-header-btns">
              <span className="ctx-btn" onClick={actions.toggleExpanded} title="折叠">−</span>
              <span className="ctx-btn" onClick={actions.toggleExpanded} title="展开">□</span>
              <span className="ctx-btn" onClick={actions.toggleExpanded} title="关闭">×</span>
            </div>
          </div>

          <div className="ctx-body">
            {/* 状态概览 */}
            <div className="ctx-section">
              <div className="ctx-status-row">
                <span className="ctx-status-indicator">{st.dot}</span>
                <span className="ctx-status-text">{st.text}</span>
                <span className="ctx-status-divider">|</span>
                <span>压缩 <b>{rounds}</b> 次</span>
                <span className="ctx-status-divider">|</span>
                <span>阈值 <b>80%</b></span>
                <span className="ctx-status-divider">|</span>
                <span><b>{fmtNum(total)}</b>/<b>{fmtNum(limit)}</b> tokens</span>
              </div>
            </div>

            {/* 用量概览 */}
            <div className="ctx-section">
              <div className="ctx-usage-row">
                <span className="ctx-usage-label">🔵 上下文占用</span>
                <div className="ctx-usage-bar">
                  <div className="ctx-bar-track">
                    <div
                      className={`ctx-bar-fill ctx-level-${level}`}
                      style={{ width: `${Math.min(pct, 100)}%` }}
                    />
                  </div>
                </div>
                <span className="ctx-usage-pct">{fmtPct}%</span>
              </div>
              <div className="ctx-usage-stats">
                <span>发送 ↑ <b>{fmtNum(status.send_tokens || 0)}</b></span>
                <span>接收 ↓ <b>{fmtNum(status.recv_tokens || 0)}</b></span>
                <span>总计 <b>{fmtNum(total)}</b></span>
                <span>| 消息 <b>{status.messages_count || 0}</b> 条</span>
              </div>
              <div className="ctx-hint">📌 超过 80% 阈值时自动在后台压缩旧对话为摘要</div>
            </div>

            {/* 控制面板 */}
            <div className="ctx-section">
              <SliderRow
                label="最大 Token"
                min={512}
                max={32768}
                step={512}
                value={limit}
                hint="滑动调整上下文窗口上限，超限的最旧消息会被丢弃"
                onChange={(v) => handleSaveConfig(v, undefined, undefined)}
              />
              <SliderRow
                label="发送上限"
                min={0}
                max={8192}
                step={128}
                value={sendLimit}
                hint="单次发送消息最大 token，超限截断"
                onChange={(v) => handleSaveConfig(undefined, v, undefined)}
              />
              <SliderRow
                label="接收上限"
                min={0}
                max={8192}
                step={128}
                value={recvLimit}
                hint="单次回复最大 token，超限截断"
                onChange={(v) => handleSaveConfig(undefined, undefined, v)}
              />
              <div className="ctx-actions">
                <button onClick={actions.compress} disabled={saving}>
                  🔄 手动压缩
                </button>
                <button onClick={actions.resetStats}>📊 重置统计</button>
              </div>
            </div>

            {/* 机制说明 */}
            <div className="ctx-section">
              <div className="ctx-help-toggle" onClick={actions.toggleHelp}>
                <span>⚙️ 机制说明</span>
                <span className="ctx-help-arrow">{helpOpen ? '▼' : '▶'}</span>
              </div>
              {helpOpen && (
                <div className="ctx-help-content">
                  <p>云枢的上下文管理采用<b>多层次自动策略</b>，无需手动干预：</p>

                  <div className="ctx-help-item">
                    <div className="ctx-help-item-title">① 异步后台压缩</div>
                    <div className="ctx-help-item-desc">
                      每次对话后检查 token 总量。当使用率超过<b>阈值（80%）</b>时，
                      在后台自动将旧对话压缩为摘要，保留关键决策和结论。
                      当前已压缩 <b>{rounds}</b> 次（摘要版本 v<span>{rounds}</span>）。
                    </div>
                  </div>

                  <div className="ctx-help-item">
                    <div className="ctx-help-item-title">② 三级水位预警</div>
                    <div className="ctx-help-item-desc">
                      <span className="ctx-legend-dot" style={{ color: '#3fb950' }}>🟢</span> <b>&lt;60%</b> 正常 ·
                      <span className="ctx-legend-dot" style={{ color: '#d29922' }}>🟡</span> <b>60%~80%</b> 接近阈值 ·
                      <span className="ctx-legend-dot" style={{ color: '#f85149' }}>🔴</span> <b>&gt;95%</b> 即将溢出
                      <br />达到 critical 时，云枢会在回复末尾提示创建新会话。
                      <br />当前：{
                        statusLevel === 'critical' ? '🔴 即将溢出（>95%）' :
                        statusLevel === 'warning' ? '🟡 接近阈值（>80%）' :
                        statusLevel === 'info' ? '🔵 接近阈值（>60%）' :
                        '🟢 正常（<60%）'
                      }
                    </div>
                  </div>

                  <div className="ctx-help-item">
                    <div className="ctx-help-item-title">③ 摘要退化检测</div>
                    <div className="ctx-help-item-desc">
                      每次压缩都会损失部分细节。<b>压缩 ≥3 次</b>时摘要质量开始下降，
                      <b>≥5 次</b>时退化明显，建议创建新会话继续对话。
                      <br />当前：{
                        rounds >= 5 ? '🔴 退化明显，建议创建新会话' :
                        rounds >= 3 ? '🟡 质量开始下降，准备换会话' :
                        '🟢 质量良好'
                      }
                    </div>
                  </div>

                  <div className="ctx-help-item">
                    <div className="ctx-help-item-title">④ System Prompt 预算保护</div>
                    <div className="ctx-help-item-desc">
                      System prompt 有 <b>10000 tokens</b> 的预算上限，
                      超限时自动截断工具状态列表，保证核心指令完整。
                    </div>
                  </div>

                  <div className="ctx-help-item">
                    <div className="ctx-help-item-title">⑤ 主动丢弃</div>
                    <div className="ctx-help-item-desc">
                      即使压缩后仍超限，系统会从<b>最旧的非摘要消息</b>开始丢弃，
                      直到满足 token_limit。
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* 最近消息 */}
            <div className="ctx-section ctx-section-msgs">
              <div className="ctx-msgs-header">📝 最近消息</div>
              <div className="ctx-msgs-list">
                {recent.length === 0 ? (
                  <div className="ctx-empty">暂无消息记录</div>
                ) : (
                  recent.map((msg, i) => {
                    const role = msg.role === 'user' ? '↑ 你' : '↓ 云枢';
                    const rc = msg.role === 'user' ? 'user' : 'assistant';
                    const preview = (msg.content_preview || '').substring(0, 50);
                    return (
                      <div key={i} className="ctx-msg-item">
                        <span className={`ctx-msg-role ${rc}`}>{role}</span>
                        <span className="ctx-msg-preview">{preview}</span>
                        <span className="ctx-msg-tokens">{fmtNum(msg.tokens || 0)}</span>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 临时通知 */}
      {notice && (
        <div className="ctx-notice" style={{ color: notice.color }}>
          {notice.text}
        </div>
      )}
    </div>
  );
};

export default ContextMonitor;
