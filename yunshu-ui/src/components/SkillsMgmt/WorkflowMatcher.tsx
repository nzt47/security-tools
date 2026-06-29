/**
 * 工作流匹配器 — 输入任务描述，匹配并尝试执行本地工作流
 *
 * 核心价值：优先本地工作流执行，避免冗余 LLM 调用
 *
 * 状态同步机制：
 * - 防连点：submitting 状态阻止重复执行
 * - 后端权威：执行后刷新工作流列表（统计已变化）
 */

import React, { useState } from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { trackEvent, type LearnedWorkflow } from '../../lib/skillsApi';
import './SkillManagement.css';

export interface WorkflowMatcherProps {
  onClose: () => void;
}

interface MatchResult {
  workflow: LearnedWorkflow;
  similarity: number;
  score: number;
}

const WorkflowMatcher: React.FC<WorkflowMatcherProps> = ({ onClose }) => {
  const { matchWorkflows, tryExecuteWorkflow, submitting, error, clearError } = useSkillsStore();
  const [taskText, setTaskText] = useState('');
  const [matches, setMatches] = useState<MatchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [execResult, setExecResult] = useState<{
    skipped: boolean;
    result?: unknown;
    reason?: string;
  } | null>(null);

  const handleMatch = async () => {
    if (!taskText.trim()) return;
    setLoading(true);
    setExecResult(null);
    try {
      const res = await matchWorkflows(taskText.trim(), 5);
      setMatches(res);
      trackEvent('workflow_match', { task_text: taskText, matched: res.length });
    } finally {
      setLoading(false);
    }
  };

  const handleTryExecute = async () => {
    if (!taskText.trim()) return;
    try {
      const res = await tryExecuteWorkflow(taskText.trim());
      setExecResult(res);
      trackEvent('workflow_try_execute', {
        task_text: taskText,
        skipped: res.skipped,
      });
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  return (
    <div
      className="skmgmt-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="skmgmt-modal" role="dialog" aria-label="工作流匹配测试">
        <div className="skmgmt-modal-header">
          <h3 className="skmgmt-modal-title">工作流匹配测试</h3>
          <button
            className="skmgmt-close-btn"
            onClick={onClose}
            type="button"
            aria-label="关闭"
          >
            ×
          </button>
        </div>

        <div className="skmgmt-modal-body">
          {error && (
            <div className="skmgmt-error-banner">
              <span>{error}</span>
              <button type="button" onClick={clearError} className="skmgmt-btn skmgmt-btn-sm">×</button>
            </div>
          )}

          <div className="skmgmt-help-tip">
            输入任务描述，系统会基于 TF-IDF 相似度 + 置信度 + 优先级综合评分匹配本地工作流。
            若最高分超过阈值，将自动执行工作流（避免冗余 LLM 调用）；否则建议交给大模型处理。
          </div>

          <div className="skmgmt-form-group">
            <label className="skmgmt-form-label">任务描述</label>
            <textarea
              className="skmgmt-form-textarea"
              style={{ minHeight: 80 }}
              value={taskText}
              onChange={(e) => setTaskText(e.target.value)}
              placeholder="例如：帮我搜索最新的 AI 论文并生成摘要"
            />
          </div>

          <div className="skmgmt-btn-row">
            <button
              className="skmgmt-btn skmgmt-btn-sm"
              onClick={handleMatch}
              disabled={loading || !taskText.trim()}
              type="button"
            >
              {loading ? '匹配中...' : '只匹配'}
            </button>
            <button
              className="skmgmt-btn skmgmt-btn-sm skmgmt-btn-primary"
              onClick={handleTryExecute}
              disabled={submitting || !taskText.trim()}
              type="button"
            >
              {submitting ? '执行中...' : '匹配并执行'}
            </button>
          </div>

          {/* ─── 执行结果 ─── */}
          {execResult && (
            <div className="skmgmt-detail-section">
              <h3 className="skmgmt-detail-section-title">执行结果</h3>
              <div className="skmgmt-review-card">
                {execResult.skipped ? (
                  <div>
                    <strong style={{ color: '#ffa726' }}>未执行本地工作流</strong>
                    <div style={{ fontSize: 12, marginTop: 4, color: 'var(--text-secondary, #8b92a0)' }}>
                      原因：{execResult.reason || '没有匹配到合适的工作流'}
                    </div>
                    <div style={{ fontSize: 12, marginTop: 4, color: 'var(--text-tertiary, #5f6675)' }}>
                      建议：交给大模型处理此任务
                    </div>
                  </div>
                ) : (
                  <div>
                    <strong style={{ color: '#66bb6a' }}>已执行本地工作流</strong>
                    <div style={{ fontSize: 12, marginTop: 4 }}>
                      {execResult.result && typeof execResult.result === 'object' && 'outputs' in (execResult.result as object)
                        ? `输出 ${(execResult.result as { outputs: unknown[] }).outputs.length} 项`
                        : '执行完成'}
                    </div>
                    <pre className="skmgmt-code-block" style={{ marginTop: 8, maxHeight: 200 }}>
                      {JSON.stringify(execResult.result, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ─── 匹配结果 ─── */}
          {matches.length > 0 && (
            <div className="skmgmt-detail-section">
              <h3 className="skmgmt-detail-section-title">匹配候选（{matches.length}）</h3>
              {matches.map((m, i) => (
                <div key={m.workflow.id} className="skmgmt-review-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <div>
                      <strong>
                        #{i + 1} {m.workflow.name}
                      </strong>
                      <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text-tertiary, #5f6675)' }}>
                        v{m.workflow.priority}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, textAlign: 'right' }}>
                      <div>相似度: <strong>{(m.similarity * 100).toFixed(1)}%</strong></div>
                      <div>综合分: <strong style={{ color: m.score >= 0.25 ? '#66bb6a' : '#ffa726' }}>
                        {(m.score * 100).toFixed(1)}
                      </strong></div>
                    </div>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary, #8b92a0)', marginTop: 4 }}>
                    {m.workflow.description}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary, #5f6675)', marginTop: 4 }}>
                    置信度 {(m.workflow.confidence * 100).toFixed(1)}% ·
                    步骤 {m.workflow.steps.length} 个 ·
                    成功率 {m.workflow.total_runs > 0
                      ? Math.round((m.workflow.success_count / m.workflow.total_runs) * 100)
                      : 0}%
                  </div>
                </div>
              ))}
            </div>
          )}

          {matches.length === 0 && taskText && !loading && (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary, #5f6675)', padding: 12 }}>
              未匹配到工作流（点击"只匹配"重新查询）
            </div>
          )}
        </div>

        <div className="skmgmt-modal-footer">
          <button className="skmgmt-btn" onClick={onClose} type="button">关闭</button>
        </div>
      </div>
    </div>
  );
};

export default WorkflowMatcher;
