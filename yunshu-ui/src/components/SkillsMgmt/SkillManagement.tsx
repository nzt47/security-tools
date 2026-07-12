/**
 * 技能管理 & 工作流学习 — 主入口组件
 *
 * 自解释 UI 设计：
 * - 顶部 Tab 切换两大模块（技能管理 / 工作流学习），Tab 上显示数量徽章
 * - 左列表 + 右详情的经典布局，让用户一眼看清"我在哪、能做什么"
 * - 每个面板都带有帮助提示条，说明该区域用途
 * - 健康检查结果以颜色徽章展示（在线/离线），帮助判断后端可用性
 */

import React, { useEffect, useState, lazy, Suspense } from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { trackEvent } from '../../lib/skillsApi';
import SkillList from './SkillList';
import SkillDetail from './SkillDetail';
import SkillCreator from './SkillCreator';
import SkillReviewer from './SkillReviewer';
import WorkflowRepo from './WorkflowRepo';
import WorkflowMatcher from './WorkflowMatcher';
import './SkillManagement.css';

// 懒加载可视化编辑器（性能策略 4：延迟加载 @xyflow/react ~150KB）
const VisualEditor = lazy(() =>
  import('../VisualEditor').then((m) => ({ default: m.VisualEditor })),
);

export type SkillMgmtTab = 'skills' | 'workflows' | 'visual-editor';

const SkillManagement: React.FC<{ onClose?: () => void }> = () => {
  const [tab, setTab] = useState<SkillMgmtTab>('skills');
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [reviewerOpen, setReviewerOpen] = useState(false);
  const [matcherOpen, setMatcherOpen] = useState(false);

  const {
    skills,
    workflows,
    skillsHealth,
    workflowHealth,
    selectedSkill,
    loadingList,
    loadAllSkills,
    loadWorkflows,
    selectSkill,
    checkHealth,
  } = useSkillsStore();

  // ─── 初始化：加载列表 + 健康检查 ───
  useEffect(() => {
    loadAllSkills();
    loadWorkflows(false);
    checkHealth();
    // 健康检查轮询：每 30 秒一次
    const timer = setInterval(checkHealth, 30000);
    return () => clearInterval(timer);
  }, [loadAllSkills, loadWorkflows, checkHealth]);

  // ─── 组件卸载时清理选中态 ───
  useEffect(() => {
    return () => {
      selectSkill(null);
    };
  }, [selectSkill]);

  const pendingCount = skills.filter(
    (s) => s.status === 'PENDING_REVIEW' || s.status === 'DRAFT',
  ).length;

  const handleTabChange = (newTab: SkillMgmtTab) => {
    trackEvent('skill_mgmt_tab_switch', { tab: newTab });
    setTab(newTab);
  };

  return (
    <div className="skill-mgmt-panel" role="dialog" aria-label="技能管理与工作流学习">
      {/* ─── 顶部标题栏 ─── */}
      <div className="skmgmt-header">
        <div className="skmgmt-title-area">
          <h2 className="skmgmt-title">技能管理 & 工作流学习</h2>
          <span className="skmgmt-subtitle">
            综合技能管理 · 工作流自动学习 · 本地优先执行
          </span>
          <HealthBadge label="技能服务" status={skillsHealth} />
          <HealthBadge label="工作流服务" status={workflowHealth} />
        </div>
      </div>

        {/* ─── Tab 切换 ─── */}
        <div className="skmgmt-tabs">
          <button
            className={`skmgmt-tab ${tab === 'skills' ? 'active' : ''}`}
            onClick={() => handleTabChange('skills')}
            type="button"
          >
            技能管理
            {skills.length > 0 && (
              <span className="skmgmt-tab-badge">{skills.length}</span>
            )}
            {pendingCount > 0 && (
              <span
                className="skmgmt-tab-badge"
                style={{ background: 'rgba(255, 152, 0, 0.2)', color: '#ffa726' }}
                title={`${pendingCount} 个待审`}
              >
                {pendingCount} 待审
              </span>
            )}
          </button>
          <button
            className={`skmgmt-tab ${tab === 'workflows' ? 'active' : ''}`}
            onClick={() => handleTabChange('workflows')}
            type="button"
          >
            工作流学习
            {workflows.length > 0 && (
              <span className="skmgmt-tab-badge">{workflows.length}</span>
            )}
          </button>
          <button
            className={`skmgmt-tab ${tab === 'visual-editor' ? 'active' : ''}`}
            onClick={() => handleTabChange('visual-editor')}
            type="button"
            title="可视化拖拽编排工作流"
          >
            可视化编辑器
          </button>

          {/* 右侧动作按钮 */}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, padding: '6px 0' }}>
            {tab === 'skills' && (
              <>
                <button
                  className="skmgmt-btn skmgmt-btn-sm"
                  onClick={() => setReviewerOpen(true)}
                  disabled={pendingCount === 0}
                  title="批量审核所有待审技能"
                  type="button"
                >
                  批量审核 {pendingCount > 0 && `(${pendingCount})`}
                </button>
                <button
                  className="skmgmt-btn skmgmt-btn-sm skmgmt-btn-primary"
                  onClick={() => setCreatorOpen(true)}
                  type="button"
                >
                  + 新建技能
                </button>
              </>
            )}
            {tab === 'workflows' && (
              <>
                <button
                  className="skmgmt-btn skmgmt-btn-sm"
                  onClick={() => setMatcherOpen(true)}
                  type="button"
                  title="输入任务描述，匹配本地工作流"
                >
                  匹配测试
                </button>
                <button
                  className="skmgmt-btn skmgmt-btn-sm skmgmt-btn-primary"
                  onClick={() => loadWorkflows(false)}
                  disabled={loadingList}
                  type="button"
                >
                  刷新
                </button>
              </>
            )}
          </div>
        </div>

        {/* ─── 主体内容 ─── */}
        <div className="skmgmt-body">
          {tab === 'skills' ? (
            <>
              <div className="skmgmt-list-pane">
                <div className="skmgmt-help-tip">
                  提示：点击左侧列表选择技能，右侧查看详情/版本/审核结果。
                  新建技能支持 AI 辅助生成、手动编写、外部安装三种方式。
                </div>
                <SkillList />
              </div>
              <div className="skmgmt-detail-pane">
                <SkillDetail />
              </div>
            </>
          ) : tab === 'workflows' ? (
            <div className="skmgmt-detail-pane" style={{ maxWidth: 'none' }}>
              <div className="skmgmt-help-tip">
                提示：智能体从大模型成功交互中学习方法，自动生成可执行工作流。
                匹配到的本地工作流会优先执行，避免冗余 LLM 调用，提升响应速度。
              </div>
              <WorkflowRepo />
            </div>
          ) : (
            <div className="skmgmt-detail-pane" style={{ maxWidth: 'none', display: 'flex', flexDirection: 'column' }}>
              <div className="skmgmt-help-tip">
                提示：从左侧面板拖拽节点到画布，连线编排工作流，右侧编辑属性，底部实时预览 YAML。
                快捷键：Delete 删除选中节点 / Ctrl+Z 撤销 / Ctrl+Y 重做。
              </div>
              <div style={{ flex: 1, minHeight: 0 }}>
                <Suspense fallback={<div style={{ padding: 24, color: '#64748b' }}>加载可视化编辑器…</div>}>
                  <VisualEditor />
                </Suspense>
              </div>
            </div>
          )}
        </div>

        {/* ─── 模态框 ─── */}
        {creatorOpen && (
          <SkillCreator
            onClose={() => setCreatorOpen(false)}
            onCreated={(skill) => {
              setCreatorOpen(false);
              selectSkill(skill.id);
            }}
          />
        )}
        {reviewerOpen && (
          <SkillReviewer
            onClose={() => setReviewerOpen(false)}
            onDone={() => {
              setReviewerOpen(false);
              loadAllSkills();
            }}
          />
        )}
        {matcherOpen && (
          <WorkflowMatcher onClose={() => setMatcherOpen(false)} />
        )}
    </div>
  );
};

// ─── 健康徽章 ───
const HealthBadge: React.FC<{ label: string; status: 'unknown' | 'online' | 'offline' }> = ({
  label,
  status,
}) => {
  const color =
    status === 'online' ? '#66bb6a' : status === 'offline' ? '#ef5350' : '#9e9e9e';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 11,
        color: 'var(--text-tertiary, #5f6675)',
        marginLeft: 8,
      }}
      title={`${label}：${status}`}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
};

export default SkillManagement;
