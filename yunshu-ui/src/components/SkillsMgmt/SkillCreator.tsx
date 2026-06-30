/**
 * 技能创建器 — 支持三种创作模式
 *
 * 三种模式：
 * 1. AI 辅助生成：输入意图描述，LLM 自动生成技能内容
 * 2. 手动编写：内置代码编辑器，支持 CODE/MARKDOWN/YAML/JSON/TEXT 五种内容类型
 * 3. 外部安装：支持 github:/url:/local:/registry: 四种源
 *
 * 状态同步机制：
 * - 防连点：submitting 状态阻止重复提交
 * - 后端权威：创建成功后用后端返回的 skill 替换列表
 */

import React, { useState } from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { trackEvent, type ContentType, type SkillCategory } from '../../lib/skillsApi';
import './SkillManagement.css';

type CreatorMode = 'ai' | 'manual' | 'install';

export interface SkillCreatorProps {
  onClose: () => void;
  onCreated: (skill: { id: string }) => void;
}

const SkillCreator: React.FC<SkillCreatorProps> = ({ onClose, onCreated }) => {
  const { createAi, createManual, install, submitting, error, clearError } = useSkillsStore();
  const [mode, setMode] = useState<CreatorMode>('ai');

  // AI 模式字段
  const [aiName, setAiName] = useState('');
  const [aiIntent, setAiIntent] = useState('');
  const [aiCategory, setAiCategory] = useState<SkillCategory>('AI_GENERATED');
  const [aiTags, setAiTags] = useState('');

  // 手动模式字段
  const [mName, setMName] = useState('');
  const [mDesc, setMDesc] = useState('');
  const [mCategory, setMCategory] = useState<SkillCategory>('CUSTOM');
  const [mContentType, setMContentType] = useState<ContentType>('CODE');
  const [mTags, setMTags] = useState('');
  const [mContent, setMContent] = useState('');
  const [mParams, setMParams] = useState('{}');

  // 安装模式字段
  const [installSource, setInstallSource] = useState('');
  const [installForce, setInstallForce] = useState(false);

  const handleSubmit = async () => {
    clearError();
    try {
      if (mode === 'ai') {
        if (!aiName.trim() || !aiIntent.trim()) {
          alert('请填写技能名称和意图描述');
          return;
        }
        const skill = await createAi({
          name: aiName.trim(),
          intent: aiIntent.trim(),
          category: aiCategory,
          tags: aiTags.split(',').map((t) => t.trim()).filter(Boolean),
        });
        trackEvent('skill_create_ai', { skill_id: skill.id });
        onCreated(skill);
      } else if (mode === 'manual') {
        if (!mName.trim() || !mContent.trim()) {
          alert('请填写技能名称和内容');
          return;
        }
        let defaultParams = {};
        try {
          defaultParams = mParams.trim() ? JSON.parse(mParams) : {};
        } catch {
          alert('默认参数必须是合法 JSON');
          return;
        }
        const skill = await createManual({
          name: mName.trim(),
          description: mDesc.trim(),
          category: mCategory,
          content_type: mContentType,
          content: mContent,
          tags: mTags.split(',').map((t) => t.trim()).filter(Boolean),
          default_params: defaultParams,
        });
        trackEvent('skill_create_manual', { skill_id: skill.id });
        onCreated(skill);
      } else {
        if (!installSource.trim()) {
          alert('请填写安装源');
          return;
        }
        const skill = await install(installSource.trim(), installForce);
        trackEvent('skill_install', { skill_id: skill.id, source: installSource });
        onCreated(skill);
      }
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
      <div className="skmgmt-modal" role="dialog" aria-label="创建技能">
        <div className="skmgmt-modal-header">
          <h3 className="skmgmt-modal-title">创建技能</h3>
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
          {/* 模式选择 */}
          <div className="skmgmt-creator-modes">
            <div
              className={`skmgmt-creator-mode ${mode === 'ai' ? 'active' : ''}`}
              onClick={() => setMode('ai')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setMode('ai');
                }
              }}
            >
              <div className="skmgmt-creator-mode-icon">✨</div>
              <div className="skmgmt-creator-mode-name">AI 辅助生成</div>
              <div className="skmgmt-creator-mode-desc">描述意图，自动生成</div>
            </div>
            <div
              className={`skmgmt-creator-mode ${mode === 'manual' ? 'active' : ''}`}
              onClick={() => setMode('manual')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setMode('manual');
                }
              }}
            >
              <div className="skmgmt-creator-mode-icon">✍️</div>
              <div className="skmgmt-creator-mode-name">手动编写</div>
              <div className="skmgmt-creator-mode-desc">内置编辑器</div>
            </div>
            <div
              className={`skmgmt-creator-mode ${mode === 'install' ? 'active' : ''}`}
              onClick={() => setMode('install')}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setMode('install');
                }
              }}
            >
              <div className="skmgmt-creator-mode-icon">📥</div>
              <div className="skmgmt-creator-mode-name">外部安装</div>
              <div className="skmgmt-creator-mode-desc">GitHub/URL/本地</div>
            </div>
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="skmgmt-error-banner">
              <span>{error}</span>
            </div>
          )}

          {/* ─── AI 模式 ─── */}
          {mode === 'ai' && (
            <>
              <div className="skmgmt-help-tip">
                输入技能名称和意图描述，AI 会自动生成技能内容、默认参数和元数据。
                生成后可进一步编辑和审核。
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">技能名称 *</label>
                <input
                  className="skmgmt-form-input"
                  value={aiName}
                  onChange={(e) => setAiName(e.target.value)}
                  placeholder="例如：邮件摘要生成器"
                />
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">意图描述 *</label>
                <textarea
                  className="skmgmt-form-textarea"
                  value={aiIntent}
                  onChange={(e) => setAiIntent(e.target.value)}
                  placeholder="详细描述技能应做什么、输入输出格式、关键约束..."
                />
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">分类</label>
                <select
                  className="skmgmt-form-select"
                  value={aiCategory}
                  onChange={(e) => setAiCategory(e.target.value as SkillCategory)}
                >
                  <option value="AI_GENERATED">AI生成</option>
                  <option value="CUSTOM">自定义</option>
                  <option value="CLAUDE">Claude</option>
                  <option value="COMMUNITY">社区</option>
                  <option value="MCP">MCP</option>
                </select>
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">标签（逗号分隔）</label>
                <input
                  className="skmgmt-form-input"
                  value={aiTags}
                  onChange={(e) => setAiTags(e.target.value)}
                  placeholder="例如：邮件,摘要,NLP"
                />
              </div>
            </>
          )}

          {/* ─── 手动模式 ─── */}
          {mode === 'manual' && (
            <>
              <div className="skmgmt-help-tip">
                手动编写技能内容。支持代码、Markdown、YAML、JSON、纯文本五种格式。
                默认参数为 JSON 对象，将在执行时作为初始参数注入。
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">技能名称 *</label>
                <input
                  className="skmgmt-form-input"
                  value={mName}
                  onChange={(e) => setMName(e.target.value)}
                  placeholder="例如：data-fetcher"
                />
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">描述</label>
                <input
                  className="skmgmt-form-input"
                  value={mDesc}
                  onChange={(e) => setMDesc(e.target.value)}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div className="skmgmt-form-group" style={{ flex: 1 }}>
                  <label className="skmgmt-form-label">分类</label>
                  <select
                    className="skmgmt-form-select"
                    value={mCategory}
                    onChange={(e) => setMCategory(e.target.value as SkillCategory)}
                  >
                    <option value="CUSTOM">自定义</option>
                    <option value="BUILTIN">内置</option>
                    <option value="CLAUDE">Claude</option>
                    <option value="COMMUNITY">社区</option>
                    <option value="MCP">MCP</option>
                    <option value="AI_GENERATED">AI生成</option>
                  </select>
                </div>
                <div className="skmgmt-form-group" style={{ flex: 1 }}>
                  <label className="skmgmt-form-label">内容类型</label>
                  <select
                    className="skmgmt-form-select"
                    value={mContentType}
                    onChange={(e) => setMContentType(e.target.value as ContentType)}
                  >
                    <option value="CODE">CODE</option>
                    <option value="MARKDOWN">MARKDOWN</option>
                    <option value="YAML">YAML</option>
                    <option value="JSON">JSON</option>
                    <option value="TEXT">TEXT</option>
                  </select>
                </div>
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">标签（逗号分隔）</label>
                <input
                  className="skmgmt-form-input"
                  value={mTags}
                  onChange={(e) => setMTags(e.target.value)}
                />
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">默认参数（JSON）</label>
                <textarea
                  className="skmgmt-form-textarea"
                  style={{ minHeight: 60 }}
                  value={mParams}
                  onChange={(e) => setMParams(e.target.value)}
                />
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">技能内容 *</label>
                <textarea
                  className="skmgmt-form-textarea"
                  style={{ minHeight: 200 }}
                  value={mContent}
                  onChange={(e) => setMContent(e.target.value)}
                  placeholder="在此编写技能内容..."
                />
              </div>
            </>
          )}

          {/* ─── 安装模式 ─── */}
          {mode === 'install' && (
            <>
              <div className="skmgmt-help-tip">
                从外部源安装技能。支持四种源格式：
                <br />• <code>github:user/repo[/path]</code> — 从 GitHub 仓库拉取
                <br />• <code>url:https://...</code> — 从 URL 下载
                <br />• <code>local:/path/to/skill.yaml</code> — 从本地路径加载
                <br />• <code>registry:skill-name</code> — 从扩展市场安装
              </div>
              <div className="skmgmt-form-group">
                <label className="skmgmt-form-label">安装源 *</label>
                <input
                  className="skmgmt-form-input"
                  value={installSource}
                  onChange={(e) => setInstallSource(e.target.value)}
                  placeholder="例如：github:myorg/skill-repo/email-summarizer"
                />
              </div>
              <div className="skmgmt-form-group">
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={installForce}
                    onChange={(e) => setInstallForce(e.target.checked)}
                  />
                  <span className="skmgmt-form-label" style={{ marginBottom: 0 }}>
                    强制覆盖（若 ID 已存在）
                  </span>
                </label>
              </div>
            </>
          )}
        </div>

        <div className="skmgmt-modal-footer">
          <button className="skmgmt-btn" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="skmgmt-btn skmgmt-btn-primary"
            onClick={handleSubmit}
            disabled={submitting}
            type="button"
          >
            {submitting ? '提交中...' : '创建'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SkillCreator;
