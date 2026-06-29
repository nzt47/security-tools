/**
 * 技能详情面板 — 展示选中技能的全部信息
 *
 * 状态同步机制：
 * - 乐观更新回滚：启用/禁用切换使用 store 内置的 try/catch + 旧状态回滚
 * - 后端权威原则：所有写操作成功后用后端返回值替换本地副本
 * - 防连点：submitting 标志位阻止重复触发
 */

import React, { useEffect, useState } from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { trackEvent } from '../../lib/skillsApi';
import './SkillManagement.css';

const SkillDetail: React.FC = () => {
  const {
    selectedSkill,
    selectedVersions,
    loadingDetail,
    loadingVersions,
    submitting,
    detailError,
    selectSkill,
    loadVersions,
    toggleSkill,
    deleteSkill,
    reviewSkill,
    updateSkill,
    bumpVersion,
    rollbackVersion,
    optimizeSkill,
    recordExecution,
  } = useSkillsStore();

  const [showEdit, setShowEdit] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editTags, setEditTags] = useState('');
  const [editContent, setEditContent] = useState('');
  const [optimizeResult, setOptimizeResult] = useState<string[] | null>(null);
  const [bumpKind, setBumpKind] = useState<'major' | 'minor' | 'patch'>('patch');
  const [bumpChangelog, setBumpChangelog] = useState('');

  // ─── 选中技能变化时：加载版本列表 + 重置编辑态 ───
  useEffect(() => {
    if (selectedSkill) {
      loadVersions(selectedSkill.id);
      setEditName(selectedSkill.name);
      setEditDesc(selectedSkill.description);
      setEditTags(selectedSkill.tags.join(', '));
      setEditContent(selectedSkill.content);
      setShowEdit(false);
      setOptimizeResult(null);
    }
  }, [selectedSkill?.id]);  // 仅依赖 id，避免对象引用变化导致重复加载

  // ─── 空状态 ───
  if (!selectedSkill) {
    return (
      <div className="skmgmt-detail-empty">
        <div className="skmgmt-detail-empty-icon">←</div>
        <div>从左侧选择一个技能查看详情</div>
        <div style={{ fontSize: 11, marginTop: 4 }}>
          或点击右上角"新建技能"创建新技能
        </div>
        {detailError && (
          <div className="skmgmt-error-banner" style={{ marginTop: 12 }}>
            <span>{detailError}</span>
          </div>
        )}
      </div>
    );
  }

  if (loadingDetail) {
    return (
      <div className="skmgmt-loading">
        <div className="skmgmt-loading-spinner" />
        <div>加载详情中...</div>
      </div>
    );
  }

  const sk = selectedSkill;

  // ─── 动作处理 ───
  const handleToggle = async () => {
    try {
      await toggleSkill(sk.id, !sk.enabled);
      trackEvent('skill_toggle', { skill_id: sk.id, enabled: !sk.enabled });
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleDelete = async () => {
    if (!confirm(`确定删除技能「${sk.name}」吗？此操作不可撤销。`)) return;
    try {
      await deleteSkill(sk.id);
      trackEvent('skill_delete', { skill_id: sk.id });
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleReview = async () => {
    try {
      await reviewSkill(sk.id);
      trackEvent('skill_review', { skill_id: sk.id });
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleSaveEdit = async () => {
    try {
      await updateSkill(sk.id, {
        name: editName,
        description: editDesc,
        tags: editTags.split(',').map((t) => t.trim()).filter(Boolean),
        content: editContent,
      });
      trackEvent('skill_update', { skill_id: sk.id });
      setShowEdit(false);
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleBump = async () => {
    try {
      await bumpVersion(sk.id, bumpKind, bumpChangelog || undefined);
      trackEvent('skill_bump_version', { skill_id: sk.id, kind: bumpKind });
      setBumpChangelog('');
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleRollback = async (target: string) => {
    if (!confirm(`确定回滚到版本 ${target} 吗？`)) return;
    try {
      await rollbackVersion(sk.id, target);
      trackEvent('skill_rollback', { skill_id: sk.id, target });
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleOptimize = async () => {
    try {
      const suggestions = await optimizeSkill(sk.id);
      setOptimizeResult(suggestions);
      trackEvent('skill_optimize', { skill_id: sk.id });
    } catch (e) {
      // 错误已在 store 中处理
    }
  };

  const handleTestExecution = async (success: boolean) => {
    await recordExecution(sk.id, success, Math.floor(Math.random() * 1000) + 100);
    trackEvent('skill_test_execution', { skill_id: sk.id, success });
  };

  return (
    <>
      {/* ─── 错误提示 ─── */}
      {detailError && (
        <div className="skmgmt-error-banner">
          <span>{detailError}</span>
        </div>
      )}

      {/* ─── 标题 + 操作按钮 ─── */}
      <h1 className="skmgmt-detail-title">{sk.name}</h1>
      <p className="skmgmt-detail-desc">{sk.description || '(无描述)'}</p>

      <div className="skmgmt-btn-row">
        <button
          className="skmgmt-btn skmgmt-btn-primary"
          onClick={handleToggle}
          disabled={submitting}
          type="button"
        >
          {sk.enabled ? '禁用' : '启用'}
        </button>
        <button
          className="skmgmt-btn"
          onClick={handleReview}
          disabled={submitting}
          type="button"
          title="触发重复检测、安全扫描、质量评估"
        >
          触发审核
        </button>
        <button
          className="skmgmt-btn"
          onClick={() => setShowEdit((v) => !v)}
          type="button"
        >
          {showEdit ? '取消编辑' : '编辑'}
        </button>
        <button
          className="skmgmt-btn"
          onClick={handleOptimize}
          disabled={submitting}
          type="button"
          title="根据使用统计给出参数优化建议"
        >
          优化建议
        </button>
        <button
          className="skmgmt-btn skmgmt-btn-danger"
          onClick={handleDelete}
          disabled={submitting}
          type="button"
        >
          删除
        </button>
      </div>

      {/* ─── 元信息 ─── */}
      <div className="skmgmt-detail-section">
        <h3 className="skmgmt-detail-section-title">基本信息</h3>
        <div className="skmgmt-meta-grid">
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">ID</div>
            <div className="skmgmt-meta-value" style={{ fontSize: 12 }}>{sk.id}</div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">分类</div>
            <div className="skmgmt-meta-value">{sk.category}</div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">状态</div>
            <div className="skmgmt-meta-value">
              <span className={`skmgmt-badge skmgmt-badge-${sk.status.toLowerCase()}`}>
                {sk.status}
              </span>
            </div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">版本</div>
            <div className="skmgmt-meta-value">v{sk.version}</div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">作者</div>
            <div className="skmgmt-meta-value">{sk.author || '—'}</div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">内容类型</div>
            <div className="skmgmt-meta-value">{sk.content_type}</div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">创建时间</div>
            <div className="skmgmt-meta-value" style={{ fontSize: 12 }}>
              {new Date(sk.created_at).toLocaleString('zh-CN')}
            </div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">更新时间</div>
            <div className="skmgmt-meta-value" style={{ fontSize: 12 }}>
              {new Date(sk.updated_at).toLocaleString('zh-CN')}
            </div>
          </div>
        </div>
        {sk.tags.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <span className="skmgmt-meta-label">标签：</span>
            {sk.tags.map((t) => (
              <span key={t} className="skmgmt-category-tag" style={{ marginRight: 4 }}>
                {t}
              </span>
            ))}
          </div>
        )}
        {sk.dependencies.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <span className="skmgmt-meta-label">依赖：</span>
            {sk.dependencies.map((d) => (
              <span key={d} className="skmgmt-category-tag" style={{ marginRight: 4 }}>
                {d}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ─── 编辑表单 ─── */}
      {showEdit && (
        <div className="skmgmt-detail-section">
          <h3 className="skmgmt-detail-section-title">编辑</h3>
          <div className="skmgmt-form-group">
            <label className="skmgmt-form-label">名称</label>
            <input
              className="skmgmt-form-input"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
            />
          </div>
          <div className="skmgmt-form-group">
            <label className="skmgmt-form-label">描述</label>
            <textarea
              className="skmgmt-form-textarea"
              style={{ minHeight: 50 }}
              value={editDesc}
              onChange={(e) => setEditDesc(e.target.value)}
            />
          </div>
          <div className="skmgmt-form-group">
            <label className="skmgmt-form-label">标签（逗号分隔）</label>
            <input
              className="skmgmt-form-input"
              value={editTags}
              onChange={(e) => setEditTags(e.target.value)}
            />
          </div>
          <div className="skmgmt-form-group">
            <label className="skmgmt-form-label">内容</label>
            <textarea
              className="skmgmt-form-textarea"
              style={{ minHeight: 200 }}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
            />
          </div>
          <button
            className="skmgmt-btn skmgmt-btn-primary"
            onClick={handleSaveEdit}
            disabled={submitting}
            type="button"
          >
            保存
          </button>
        </div>
      )}

      {/* ─── 内容预览 ─── */}
      <div className="skmgmt-detail-section">
        <h3 className="skmgmt-detail-section-title">技能内容</h3>
        <pre className="skmgmt-code-block">{sk.content}</pre>
      </div>

      {/* ─── 审核结果 ─── */}
      {sk.review && (
        <div className="skmgmt-detail-section">
          <h3 className="skmgmt-detail-section-title">
            审核结果（{new Date(sk.review.reviewed_at).toLocaleString('zh-CN')}）
          </h3>
          <div className="skmgmt-review-card">
            <div className="skmgmt-review-scores">
              <div className="skmgmt-review-score">
                <div className="skmgmt-review-score-label">综合</div>
                <div className="skmgmt-review-score-value">
                  {Math.round(sk.review.overall_score)}
                </div>
              </div>
              <div className="skmgmt-review-score">
                <div className="skmgmt-review-score-label">安全</div>
                <div className="skmgmt-review-score-value">
                  {Math.round(sk.review.security_score)}
                </div>
              </div>
              <div className="skmgmt-review-score">
                <div className="skmgmt-review-score-label">质量</div>
                <div className="skmgmt-review-score-value">
                  {Math.round(sk.review.quality_score)}
                </div>
              </div>
              <div className="skmgmt-review-score">
                <div className="skmgmt-review-score-label">原创</div>
                <div className="skmgmt-review-score-value">
                  {Math.round(sk.review.duplicate_score)}
                </div>
              </div>
            </div>
            <div className="skmgmt-meta-label" style={{ marginBottom: 6 }}>发现项：</div>
            {sk.review.findings.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary, #5f6675)' }}>
                无发现项
              </div>
            ) : (
              sk.review.findings.map((f, i) => (
                <div key={i} className={`skmgmt-finding skmgmt-finding-${f.severity}`}>
                  <strong>[{f.severity}]</strong> {f.type}: {f.message}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* ─── 优化建议 ─── */}
      {optimizeResult && (
        <div className="skmgmt-detail-section">
          <h3 className="skmgmt-detail-section-title">优化建议</h3>
          {optimizeResult.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary, #5f6675)' }}>
              暂无优化建议，当前参数配置良好。
            </div>
          ) : (
            <ul style={{ fontSize: 13, paddingLeft: 20 }}>
              {optimizeResult.map((s, i) => (
                <li key={i} style={{ marginBottom: 4 }}>{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ─── 使用统计 ─── */}
      <div className="skmgmt-detail-section">
        <h3 className="skmgmt-detail-section-title">使用统计</h3>
        <div className="skmgmt-meta-grid">
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">总调用</div>
            <div className="skmgmt-meta-value">{sk.metrics.usage_count}</div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">成功</div>
            <div className="skmgmt-meta-value" style={{ color: '#66bb6a' }}>
              {sk.metrics.success_count}
            </div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">失败</div>
            <div className="skmgmt-meta-value" style={{ color: '#ef5350' }}>
              {sk.metrics.failure_count}
            </div>
          </div>
          <div className="skmgmt-meta-item">
            <div className="skmgmt-meta-label">平均耗时</div>
            <div className="skmgmt-meta-value">{Math.round(sk.metrics.avg_latency_ms)} ms</div>
          </div>
          {sk.metrics.last_used_at && (
            <div className="skmgmt-meta-item">
              <div className="skmgmt-meta-label">最后使用</div>
              <div className="skmgmt-meta-value" style={{ fontSize: 12 }}>
                {new Date(sk.metrics.last_used_at).toLocaleString('zh-CN')}
              </div>
            </div>
          )}
        </div>
        <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
          <button
            className="skmgmt-btn skmgmt-btn-sm"
            onClick={() => handleTestExecution(true)}
            type="button"
          >
            记录一次成功
          </button>
          <button
            className="skmgmt-btn skmgmt-btn-sm"
            onClick={() => handleTestExecution(false)}
            type="button"
          >
            记录一次失败
          </button>
        </div>
      </div>

      {/* ─── 版本管理 ─── */}
      <div className="skmgmt-detail-section">
        <h3 className="skmgmt-detail-section-title">
          版本历史 {loadingVersions && '(加载中...)'}
        </h3>

        {/* 版本升级 */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <select
            className="skmgmt-form-select"
            style={{ width: 'auto' }}
            value={bumpKind}
            onChange={(e) => setBumpKind(e.target.value as 'major' | 'minor' | 'patch')}
          >
            <option value="patch">Patch（修订）</option>
            <option value="minor">Minor（次版本）</option>
            <option value="major">Major（主版本）</option>
          </select>
          <input
            className="skmgmt-form-input"
            style={{ flex: 1, minWidth: 200 }}
            placeholder="变更说明（可选）"
            value={bumpChangelog}
            onChange={(e) => setBumpChangelog(e.target.value)}
          />
          <button
            className="skmgmt-btn skmgmt-btn-primary skmgmt-btn-sm"
            onClick={handleBump}
            disabled={submitting}
            type="button"
          >
            发布新版本
          </button>
        </div>

        {/* 版本列表 */}
        {selectedVersions.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary, #5f6675)' }}>
            暂无历史版本
          </div>
        ) : (
          selectedVersions.map((v) => (
            <div key={v.version} className="skmgmt-review-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <strong>v{v.version}</strong>
                  {v.version === sk.version && (
                    <span
                      className="skmgmt-badge skmgmt-badge-enabled"
                      style={{ marginLeft: 6 }}
                    >
                      当前
                    </span>
                  )}
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary, #5f6675)', marginTop: 2 }}>
                    {new Date(v.created_at).toLocaleString('zh-CN')}
                  </div>
                  {v.changelog && (
                    <div style={{ fontSize: 12, marginTop: 4 }}>{v.changelog}</div>
                  )}
                </div>
                {v.version !== sk.version && (
                  <button
                    className="skmgmt-btn skmgmt-btn-sm"
                    onClick={() => handleRollback(v.version)}
                    disabled={submitting}
                    type="button"
                  >
                    回滚到此版本
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );
};

export default SkillDetail;
