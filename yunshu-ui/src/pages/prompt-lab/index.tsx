/**
 * PromptLab —— 提示词影响因素管理面板（主页编排）
 * ------------------------------------------------
 * 布局：顶栏（标题 + 模块 Tab 条）+ 分类筛选 + 主体（左因素区 / 右预览 PreviewPanel）
 *      / LLM 通信监控 Tab（全宽，LlmMonitorPanel）
 * 功能：5 类因素调节（滑块/下拉/文本/开关）、实时预览（模拟 + 真实 LLM）、
 *       分类筛选、自定义因素添加/删除、JSON/CSV 导出。
 *
 * 【深度合并（身份提示词 + LLM 通信并入）】
 *   - 「系统提示词」区不再本地沙箱：由 useIdentityPrompt 直接编辑后端
 *     「身份提示词」线上配置（启停/自定义内容/保存/重置），预览与
 *     「请求真实输出」注入的 system message 由后端模板引擎生成。
 *   - 原页面**最底部**的「LLM 通信监控」已提到**主内容区**，与「提示词影响因素
 *     实验室」并列为一个 Tab（req：从最后位置移至主内容区，TAB 形式并列展示）。
 *   - 顶栏不再提供「返回工作台」按钮（工作台内导航由左侧导航树负责）。
 *
 * 子组件拆分见 prompt-lab/ 目录：RadarChart / FactorControl / FactorCard /
 * CustomFactorForm / PreviewPanel / IdentityPromptPanel / LlmMonitorPanel。
 */
import { useEffect, useMemo, useState } from 'react';
import { FlaskConical, Plus, Radio, RotateCcw } from 'lucide-react';
import { usePromptLabStore } from '../../stores/usePromptLabStore';
import { downloadFile } from '../../utils/system';
import {
  CATEGORIES,
  allFactors,
  buildPrompt,
  exportCsv,
  exportJson,
  factorsOfCategory,
  numOf,
  radarData,
  requestLlmPreview,
  simulateOutput,
  usageReport,
} from '../../lib/promptFactors';
import type { FactorCategory, FactorValue } from '../../lib/promptFactorTypes';
import FactorCard from './FactorCard';
import CustomFactorForm from './CustomFactorForm';
import PreviewPanel, { type PreviewMode } from './PreviewPanel';
import IdentityPromptPanel, { toIdentityPromptPanelProps } from './IdentityPromptPanel';
import LlmMonitorPanel from './LlmMonitorPanel';
import { useIdentityPrompt } from './identityPrompt';
import '../PromptLab.css';

type Filter = 'all' | FactorCategory;

/** 主内容区模块 Tab（并列展示）：影响因素实验室 / LLM 通信监控 */
type LabTab = 'factors' | 'llm-monitor';

const LAB_TABS: { id: LabTab; label: string; icon: typeof FlaskConical; hint: string }[] = [
  { id: 'factors', label: '提示词影响因素实验室', icon: FlaskConical, hint: '5 类影响因素调节 + 身份提示词线上配置 + 实时预览' },
  { id: 'llm-monitor', label: 'LLM 通信监控', icon: Radio, hint: '每次 LLM 调用的完整收发内容（折叠展开）+ Token 统计' },
];

/** 记住上次所在 Tab（会话级偏好，刷新不跳回） */
const TAB_STORAGE_KEY = 'yunshu.prompt-lab.tab';

const readSavedTab = (): LabTab => {
  try {
    const v = localStorage.getItem(TAB_STORAGE_KEY);
    return v === 'llm-monitor' ? 'llm-monitor' : 'factors';
  } catch {
    return 'factors';
  }
};

const dateTag = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

export default function PromptLab() {
  const values = usePromptLabStore((s) => s.values);
  const customFactors = usePromptLabStore((s) => s.customFactors);
  const llm = usePromptLabStore((s) => s.llm);
  const setValue = usePromptLabStore((s) => s.setValue);
  const removeCustomFactor = usePromptLabStore((s) => s.removeCustomFactor);
  const resetValues = usePromptLabStore((s) => s.resetValues);

  // 深度合并：线上「身份提示词」配置（后端模板 = 注入 system message 的唯一来源）
  const identity = useIdentityPrompt();

  const [tab, setTab] = useState<LabTab>(readSavedTab);
  const [filter, setFilter] = useState<Filter>('all');
  const [showForm, setShowForm] = useState(false);
  const [mode, setMode] = useState<PreviewMode>('sim');
  // 身份提示词面板聚焦项：悬停某面板项 → 右侧提示词区域高亮该节的发出内容
  const [focusRowKey, setFocusRowKey] = useState<string | null>(null);
  const [llmOutput, setLlmOutput] = useState<string | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(TAB_STORAGE_KEY, tab);
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [tab]);

  const defs = useMemo(() => allFactors(customFactors), [customFactors]);

  const prompt = useMemo(() => buildPrompt(values), [values]);
  const sim = useMemo(() => simulateOutput(values), [values]);
  const radar = useMemo(() => radarData(values), [values]);
  const systemPrompt = identity.template;
  const token = useMemo(
    () => usageReport(systemPrompt, prompt, llm.contextWindow),
    [systemPrompt, prompt, llm.contextWindow],
  );

  const shownCategories = useMemo(
    () => (filter === 'all' ? CATEGORIES : CATEGORIES.filter((c) => c.id === filter)),
    [filter],
  );

  const runLlm = async () => {
    if (!llm.endpoint.trim()) {
      setLlmError('请先在上方"真实接口"设置中填写 Endpoint。');
      return;
    }
    setLlmLoading(true);
    setLlmError(null);
    const res = await requestLlmPreview({
      endpoint: llm.endpoint.trim(),
      apiKey: llm.apiKey,
      model: llm.model,
      systemPrompt,
      prompt,
      temperature: numOf(values, 'temperature'),
      topP: numOf(values, 'top_p'),
      maxTokens: numOf(values, 'max_tokens'),
    });
    setLlmLoading(false);
    if (res.ok === true) {
      setLlmOutput(res.text);
      setLlmError(null);
    } else {
      setLlmError(res.error);
    }
  };

  const onValueChange = (id: string, v: FactorValue) => {
    setValue(id, v);
    setLlmOutput(null); // 参数变化后旧的真实输出失效，回到模拟预览
  };

  const onModeChange = (m: PreviewMode) => {
    setMode(m);
    setLlmError(null);
  };

  const exportWith = (kind: 'json' | 'csv') => {
    const tag = dateTag();
    if (kind === 'json')
      downloadFile(`prompt-factors-${tag}.json`, exportJson(values, customFactors, systemPrompt, llm.contextWindow), 'application/json');
    else downloadFile(`prompt-factors-${tag}.csv`, exportCsv(values, customFactors), 'text/csv;charset=utf-8');
  };

  const activeTab = LAB_TABS.find((t) => t.id === tab) ?? LAB_TABS[0];
  const ActiveTabIcon = activeTab.icon;

  return (
    <div className="pl-root">
      {/* 顶栏 */}
      <header className="pl-topbar">
        <div className="flex items-center gap-2.5">
          <div className="pl-logo-badge">
            <ActiveTabIcon size={15} />
          </div>
          <div>
            <h1 className="pl-title">提示词实验室</h1>
            <p className="pl-subtitle">{activeTab.hint}</p>
          </div>
        </div>
        <div className="pl-topbar-actions">
          {tab === 'factors' && (
            <>
              <button type="button" className="pl-btn" onClick={() => setShowForm(true)}>
                <Plus size={13} />
                添加因素
              </button>
              <button type="button" className="pl-btn" onClick={resetValues} title="所有因素恢复默认值">
                <RotateCcw size={13} />
                重置
              </button>
            </>
          )}
        </div>
      </header>

      {/* 主内容区 Tab 条：影响因素实验室 / LLM 通信监控 并列展示 */}
      <nav className="pl-tabs" role="tablist" aria-label="提示词实验室模块">
        {LAB_TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`pl-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
              title={t.hint}
            >
              <Icon size={13} />
              {t.label}
            </button>
          );
        })}
      </nav>

      {tab === 'factors' ? (
        <>
          {/* 分类筛选 */}
          <nav className="pl-filter" aria-label="因素分类筛选">
            {(['all', ...CATEGORIES.map((c) => c.id)] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                className={`pl-filter-chip ${filter === f ? 'active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? '全部' : CATEGORIES.find((c) => c.id === f)?.short}
              </button>
            ))}
          </nav>

          <div className="pl-body">
            {/* 左侧：因素模块 + 身份提示词 */}
            <main className="pl-factors">
              {/* 身份提示词（线上配置）——原「人格与提示词 → 身份提示词」并入 */}
              <IdentityPromptPanel
                {...toIdentityPromptPanelProps(identity)}
                focusedKey={focusRowKey}
                onFocusRow={setFocusRowKey}
              />

              {shownCategories.map((cat) => (
                <section key={cat.id} className="pl-category">
                  <h2 className="pl-category-title" style={{ color: cat.color }}>
                    <span className="pl-category-dot" style={{ background: cat.color }} />
                    {cat.label}
                    <span className="pl-category-count">
                      {factorsOfCategory(defs, cat.id).length} 项
                    </span>
                  </h2>
                  <p className="pl-category-desc">{cat.desc}</p>
                  <div className="pl-card-grid">
                    {factorsOfCategory(defs, cat.id).map((def) => (
                      <FactorCard
                        key={def.id}
                        def={def}
                        value={values[def.id] ?? def.defaultValue}
                        onChange={(v) => onValueChange(def.id, v)}
                        onRemove={removeCustomFactor}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </main>

            {/* 右侧：实时预览面板（PreviewPanel） */}
            <PreviewPanel
              mode={mode}
              onModeChange={onModeChange}
              systemPrompt={systemPrompt}
              highlightSnippet={focusRowKey ? identity.rows.find((r) => r.key === focusRowKey)?.emitText : null}
              prompt={prompt}
              sim={sim}
              radar={radar}
              token={token}
              llmOutput={llmOutput}
              llmError={llmError}
              llmLoading={llmLoading}
              onRunLlm={() => void runLlm()}
              onExport={exportWith}
            />
          </div>
        </>
      ) : (
        /* LLM 通信监控：主内容区整屏（原页面底部并入 → 提为主 Tab） */
        <div className="pl-tab-body">
          <LlmMonitorPanel />
        </div>
      )}

      {showForm && <CustomFactorForm onClose={() => setShowForm(false)} />}
    </div>
  );
}
