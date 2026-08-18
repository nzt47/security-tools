/**
 * 技能列表 — 搜索 / 筛选 / 排序
 *
 * 状态同步机制：
 * - debounce 防抖：搜索输入 300ms 防抖，防止高频请求打乱状态
 * - AbortController + Request ID：通过 store 内置的双重防竞态，仅最新请求结果更新 UI
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useSkillsStore } from '../../store/skillsStore';
import { debounce, trackEvent, type SkillCategory, type SkillStatus } from '../../lib/skillsApi';
import './SkillManagement.css';

const ALL_CATEGORIES: SkillCategory[] = [
  'BUILTIN', 'CUSTOM', 'CLAUDE', 'COMMUNITY', 'MCP', 'AI_GENERATED',
];
const ALL_STATUSES: SkillStatus[] = [
  'DRAFT', 'PENDING_REVIEW', 'APPROVED', 'PUBLISHED', 'REJECTED', 'DEPRECATED', 'ARCHIVED',
];

const SkillList: React.FC = () => {
  const {
    skills,
    loadingList,
    error,
    selectedSkill,
    searchSkills,
    loadAllSkills,
    selectSkill,
    clearError,
  } = useSkillsStore();

  const [query, setQuery] = useState('');
  const [activeCategories, setActiveCategories] = useState<SkillCategory[]>([]);
  const [activeStatuses, setActiveStatuses] = useState<SkillStatus[]>([]);
  const [enabledOnly, setEnabledOnly] = useState(false);

  // ─── 搜索防抖：300ms ───
  // 通过 useRef 保持同一个 debounced 函数实例，避免每次渲染重建
  const debouncedSearchRef = useRef(
    debounce((q: string, cats: SkillCategory[], sts: SkillStatus[], en: boolean) => {
      const hasFilter = q.trim() || cats.length > 0 || sts.length > 0 || en;
      if (!hasFilter) {
        loadAllSkills();
        return;
      }
      searchSkills({
        q: q.trim() || undefined,
        categories: cats.length > 0 ? cats : undefined,
        statuses: sts.length > 0 ? sts : undefined,
        enabled_only: en || undefined,
        page: 1,
        page_size: 50,
      });
    }, 300),
  );

  useEffect(() => {
    debouncedSearchRef.current(query, activeCategories, activeStatuses, enabledOnly);
  }, [query, activeCategories, activeStatuses, enabledOnly]);

  const toggleCategory = (cat: SkillCategory) => {
    setActiveCategories((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat],
    );
  };
  const toggleStatus = (st: SkillStatus) => {
    setActiveStatuses((prev) =>
      prev.includes(st) ? prev.filter((s) => s !== st) : [...prev, st],
    );
  };

  const handleSelect = (skillId: string) => {
    trackEvent('skill_select', { skill_id: skillId });
    selectSkill(skillId);
  };

  // ─── 分类与状态展示文案 ───
  const categoryLabel = useMemo(() => ({
    BUILTIN: '内置', CUSTOM: '自定义', CLAUDE: 'Claude',
    COMMUNITY: '社区', MCP: 'MCP', AI_GENERATED: 'AI生成',
  } as const), []);
  const statusLabel = useMemo(() => ({
    DRAFT: '草稿', PENDING_REVIEW: '待审', APPROVED: '已通过',
    PUBLISHED: '已发布', REJECTED: '已拒绝', DEPRECATED: '已弃用', ARCHIVED: '已归档',
  } as const), []);

  return (
    <>
      {/* ─── 搜索栏 ─── */}
      <div className="skmgmt-search-bar">
        <div className="skmgmt-search-input-wrap">
          <span className="skmgmt-search-icon">🔍</span>
          <input
            className="skmgmt-search-input"
            type="text"
            placeholder="按名称、描述、标签搜索..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="搜索技能"
          />
        </div>

        {/* 分类筛选 */}
        <div className="skmgmt-filter-row">
          {ALL_CATEGORIES.map((cat) => (
            <button
              key={cat}
              type="button"
              className={`skmgmt-filter-chip ${activeCategories.includes(cat) ? 'active' : ''}`}
              onClick={() => toggleCategory(cat)}
            >
              {categoryLabel[cat]}
            </button>
          ))}
        </div>

        {/* 状态筛选 */}
        <div className="skmgmt-filter-row">
          {ALL_STATUSES.map((st) => (
            <button
              key={st}
              type="button"
              className={`skmgmt-filter-chip ${activeStatuses.includes(st) ? 'active' : ''}`}
              onClick={() => toggleStatus(st)}
            >
              {statusLabel[st]}
            </button>
          ))}
          <button
            type="button"
            className={`skmgmt-filter-chip ${enabledOnly ? 'active' : ''}`}
            onClick={() => setEnabledOnly((v) => !v)}
            style={{ marginLeft: 'auto' }}
          >
            仅启用
          </button>
        </div>
      </div>

      {/* ─── 错误提示 ─── */}
      {error && (
        <div className="skmgmt-error-banner">
          <span>{error}</span>
          <button type="button" onClick={clearError} className="skmgmt-btn skmgmt-btn-sm">×</button>
        </div>
      )}

      {/* ─── 列表 ─── */}
      <div className="skmgmt-list">
        {loadingList ? (
          <div className="skmgmt-loading">
            <div className="skmgmt-loading-spinner" />
            <div>加载中...</div>
          </div>
        ) : skills.length === 0 ? (
          <div className="skmgmt-detail-empty">
            <div className="skmgmt-detail-empty-icon">∅</div>
            <div>暂无技能</div>
            <div style={{ fontSize: 11, marginTop: 4 }}>
              使用右上角"新建技能"按钮创建第一个技能
            </div>
          </div>
        ) : (
          skills.map((skill) => (
            <div
              key={skill.id}
              className={`skmgmt-list-item ${selectedSkill?.id === skill.id ? 'selected' : ''}`}
              onClick={() => handleSelect(skill.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  handleSelect(skill.id);
                }
              }}
            >
              <div className="skmgmt-item-title">
                <span>{skill.name}</span>
                <span className={`skmgmt-badge skmgmt-badge-${skill.enabled ? 'enabled' : 'disabled'}`}>
                  {skill.enabled ? '启用' : '停用'}
                </span>
              </div>
              <p className="skmgmt-item-desc">
                {skill.description || '(无描述)'}
              </p>
              <div className="skmgmt-item-meta">
                <span className="skmgmt-category-tag">{categoryLabel[skill.category]}</span>
                <span className={`skmgmt-badge skmgmt-badge-${skill.status.toLowerCase()}`}>
                  {statusLabel[skill.status]}
                </span>
                <span>v{skill.version}</span>
                {skill.metrics.usage_count > 0 && (
                  <span title="使用次数">· 用 {skill.metrics.usage_count} 次</span>
                )}
                {skill.review && (
                  <span title="综合得分">· 评 {Math.round(skill.review.overall_score)}</span>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </>
  );
};

export default SkillList;
