/**
 * 批量审核面板 — 集中审核所有待审技能
 *
 * 自解释 UI：
 * - 显示阈值配置，让用户理解"什么样的技能会通过"
 * - 列出每个待审技能的审核结果（评分 + 发现项）
 * - 阈值可在线调整并保存
 */

import React, { useEffect, useState } from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { skillsApi, trackEvent } from '../../lib/skillsApi';
import './SkillManagement.css';

export interface SkillReviewerProps {
  onClose: () => void;
  onDone: () => void;
}

const SkillReviewer: React.FC<SkillReviewerProps> = ({ onClose, onDone }) => {
  const { skills, submitting, error, reviewAllPending, loadAllSkills, clearError } = useSkillsStore();
  const [thresholds, setThresholds] = useState<Record<string, number>>({
    duplicate_max: 60,
    security_min: 70,
    quality_min: 50,
    overall_min: 60,
  });
  const [savingThresholds, setSavingThresholds] = useState(false);

  const pendingSkills = skills.filter(
    (s) => s.status === 'PENDING_REVIEW' || s.status === 'DRAFT',
  );

  useEffect(() => {
    skillsApi.getThresholds().then((res) => {
      setThresholds(res.thresholds);
    }).catch(() => {
      // 阈值加载失败使用默认值
    });
  }, []);

  const handleBatchReview = async () => {
    try {
      const reviewed = await reviewAllPending();
      trackEvent('skill_batch_review', { reviewed });
      alert(`已审核 ${reviewed} 个技能`);
      onDone();
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleSaveThresholds = async () => {
    setSavingThresholds(true);
    try {
      await skillsApi.updateThresholds(thresholds);
      alert('阈值已保存');
      // 重新加载列表（阈值变化可能影响状态判定）
      await loadAllSkills();
    } catch (e) {
      alert('保存失败: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSavingThresholds(false);
    }
  };

  return (
    <div
      className="skmgmt-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="skmgmt-modal" role="dialog" aria-label="批量审核">
        <div className="skmgmt-modal-header">
          <h3 className="skmgmt-modal-title">批量审核（{pendingSkills.length} 个待审）</h3>
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

          {/* 阈值配置 */}
          <div className="skmgmt-detail-section">
            <h3 className="skmgmt-detail-section-title">审核阈值</h3>
            <div className="skmgmt-help-tip">
              评分含义：综合分 = 安全分×0.5 + 质量分×0.3 + 原创分×0.2。
              各项分数低于对应下限或重复度高于上限的技能将被拒绝。
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {([
                ['duplicate_max', '重复度上限（0-100）'],
                ['security_min', '安全分下限（0-100）'],
                ['quality_min', '质量分下限（0-100）'],
                ['overall_min', '综合分下限（0-100）'],
              ] as const).map(([key, label]) => (
                <div key={key} className="skmgmt-form-group" style={{ marginBottom: 0 }}>
                  <label className="skmgmt-form-label">{label}</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    className="skmgmt-form-input"
                    value={thresholds[key] ?? 0}
                    onChange={(e) =>
                      setThresholds((prev) => ({ ...prev, [key]: Number(e.target.value) }))
                    }
                  />
                </div>
              ))}
            </div>
            <button
              className="skmgmt-btn skmgmt-btn-sm"
              style={{ marginTop: 10 }}
              onClick={handleSaveThresholds}
              disabled={savingThresholds}
              type="button"
            >
              {savingThresholds ? '保存中...' : '保存阈值'}
            </button>
          </div>

          {/* 待审技能列表 */}
          <div className="skmgmt-detail-section">
            <h3 className="skmgmt-detail-section-title">待审技能</h3>
            {pendingSkills.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary, #5f6675)', padding: 12 }}>
                暂无待审技能
              </div>
            ) : (
              pendingSkills.map((sk) => (
                <div key={sk.id} className="skmgmt-review-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <div>
                      <strong>{sk.name}</strong>
                      <span className="skmgmt-category-tag" style={{ marginLeft: 6 }}>
                        {sk.category}
                      </span>
                    </div>
                    <span className={`skmgmt-badge skmgmt-badge-${sk.status.toLowerCase()}`}>
                      {sk.status}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary, #8b92a0)', marginTop: 4 }}>
                    {sk.description || '(无描述)'}
                  </div>
                  {sk.review && (
                    <div style={{ marginTop: 8, fontSize: 12 }}>
                      综合分 {Math.round(sk.review.overall_score)} ·
                      安全 {Math.round(sk.review.security_score)} ·
                      质量 {Math.round(sk.review.quality_score)} ·
                      原创 {Math.round(sk.review.duplicate_score)}
                      <span
                        className={`skmgmt-badge skmgmt-badge-${
                          sk.review.status === 'PASSED' ? 'approved' :
                          sk.review.status === 'REJECTED' ? 'rejected' : 'pending_review'
                        }`}
                        style={{ marginLeft: 8 }}
                      >
                        {sk.review.status}
                      </span>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="skmgmt-modal-footer">
          <button className="skmgmt-btn" onClick={onClose} type="button">取消</button>
          <button
            className="skmgmt-btn skmgmt-btn-primary"
            onClick={handleBatchReview}
            disabled={submitting || pendingSkills.length === 0}
            type="button"
          >
            {submitting ? '审核中...' : `一键审核 ${pendingSkills.length} 个`}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SkillReviewer;
