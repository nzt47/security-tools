/**
 * 工作流仓库 — 展示从大模型交互中学习到的工作流
 *
 * 自解释 UI：
 * - 每个工作流卡片展示：名称、描述、置信度、成功率、步骤列表
 * - 步骤列表展示工具链，让用户一眼看清工作流逻辑
 * - 支持启用/禁用、删除、调整优先级
 */

import React from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { trackEvent } from '../../lib/skillsApi';
import './SkillManagement.css';

const WorkflowRepo: React.FC = () => {
  const {
    workflows,
    loadingWorkflows,
    submitting,
    toggleWorkflow,
    deleteWorkflow,
    setWorkflowPriority,
    loadWorkflows,
  } = useSkillsStore();

  if (loadingWorkflows) {
    return (
      <div className="skmgmt-loading">
        <div className="skmgmt-loading-spinner" />
        <div>加载工作流中...</div>
      </div>
    );
  }

  if (workflows.length === 0) {
    return (
      <div className="skmgmt-detail-empty">
        <div className="skmgmt-detail-empty-icon">∅</div>
        <div>暂无已学习的工作流</div>
        <div style={{ fontSize: 11, marginTop: 4 }}>
          当智能体成功完成多步骤任务后，会自动学习方法并生成可复用工作流
        </div>
      </div>
    );
  }

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 className="skmgmt-detail-section-title" style={{ marginBottom: 0, borderBottom: 'none' }}>
          已学习工作流（共 {workflows.length} 个）
        </h3>
        <button
          className="skmgmt-btn skmgmt-btn-sm"
          onClick={() => loadWorkflows(false)}
          type="button"
        >
          刷新
        </button>
      </div>

      {workflows.map((wf) => {
        const successRate = wf.total_runs > 0
          ? Math.round((wf.success_count / wf.total_runs) * 100)
          : 0;
        return (
          <div key={wf.id} className="skmgmt-workflow-card">
            <div className="skmgmt-workflow-header">
              <div>
                <h4 className="skmgmt-workflow-name">
                  {wf.name}
                  <span
                    className={`skmgmt-badge skmgmt-badge-${wf.enabled ? 'enabled' : 'disabled'}`}
                    style={{ marginLeft: 8 }}
                  >
                    {wf.enabled ? '启用' : '停用'}
                  </span>
                </h4>
              </div>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--text-tertiary, #5f6675)' }}>优先级</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={wf.priority}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (!Number.isNaN(v) && v >= 0 && v <= 100) {
                      setWorkflowPriority(wf.id, v);
                    }
                  }}
                  style={{
                    width: 50,
                    background: 'var(--bg-input, #0f1116)',
                    border: '1px solid var(--border-subtle, #2a2e38)',
                    color: 'var(--text-primary, #e8eaed)',
                    borderRadius: 4,
                    padding: '2px 4px',
                    fontSize: 12,
                  }}
                  title="调整优先级（0-100，越高越优先匹配）"
                />
              </div>
            </div>

            <p className="skmgmt-workflow-desc">{wf.description}</p>

            <div className="skmgmt-workflow-stats">
              <span title="置信度 = 成功率 × (1 - e^(-总次数/5))">
                置信度: <strong>{(wf.confidence * 100).toFixed(1)}%</strong>
              </span>
              <span>
                成功率: <strong style={{ color: successRate >= 70 ? '#66bb6a' : '#ffa726' }}>
                  {successRate}%
                </strong>
                <span style={{ color: 'var(--text-tertiary, #5f6675)' }}>
                  {' '}({wf.success_count}/{wf.total_runs})
                </span>
              </span>
              <span>步骤: {wf.steps.length}</span>
              {wf.keywords.length > 0 && (
                <span title="关键词">
                  关键词: {wf.keywords.slice(0, 5).join(', ')}
                  {wf.keywords.length > 5 && '...'}
                </span>
              )}
            </div>

            <div className="skmgmt-workflow-steps">
              <div style={{ marginBottom: 4, color: 'var(--text-tertiary, #5f6675)' }}>
                执行步骤：
              </div>
              <ol>
                {wf.steps.map((step, i) => (
                  <li key={step.id || i}>
                    <strong>{step.name}</strong>
                    <span style={{ color: 'var(--text-tertiary, #5f6675)' }}>
                      {' '}→ {step.tool_name}
                    </span>
                    {step.condition && (
                      <span style={{ color: '#ffa726', fontSize: 10, marginLeft: 6 }}>
                        [{step.condition}]
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>

            <div className="skmgmt-workflow-actions">
              <button
                className="skmgmt-btn skmgmt-btn-sm"
                onClick={() => {
                  toggleWorkflow(wf.id, !wf.enabled);
                  trackEvent('workflow_toggle', { wf_id: wf.id, enabled: !wf.enabled });
                }}
                disabled={submitting}
                type="button"
              >
                {wf.enabled ? '禁用' : '启用'}
              </button>
              <button
                className="skmgmt-btn skmgmt-btn-sm skmgmt-btn-danger"
                onClick={() => {
                  if (confirm(`确定删除工作流「${wf.name}」吗？`)) {
                    deleteWorkflow(wf.id);
                    trackEvent('workflow_delete', { wf_id: wf.id });
                  }
                }}
                disabled={submitting}
                type="button"
              >
                删除
              </button>
            </div>
          </div>
        );
      })}
    </>
  );
};

export default WorkflowRepo;
