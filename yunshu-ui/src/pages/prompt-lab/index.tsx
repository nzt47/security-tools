/**
 * PromptLab —— 提示词影响因素管理面板（主页面编排）
 * ------------------------------------------------
 * 布局：顶部（返回/标题/添加因素/重置）+ 分类筛选 + 左侧因素模块 + 右侧预览（PreviewPanel）
 * 功能：5 类因素调节（滑块/下拉/文本/开关）、实时预览（模拟 + 预留真实 LLM）、
 *       分类筛选、自定义因素添加/删除、JSON/CSV 导出。
 * 路由：独立页面 #/prompt-lab（不进 Mosaic 布局，顶部导航进入）。
 *
 * 子组件拆分见 prompt-lab/ 目录：RadarChart / FactorControl / FactorCard /
 * SystemPartCard / AddSystemPartForm / CustomFactorForm / PreviewPanel
 * （本文件仅保留状态与左侧编排）。
 */
import { useMemo, useState } from 'react';
import { ArrowLeft, FlaskConical, Plus, RotateCcw } from 'lucide-react';
import { usePromptLabStore } from '../../stores/usePromptLabStore';
import { downloadFile } from '../../utils/system';
import {
  CATEGORIES,
  allFactors,
  assembleSystemPrompt,
  buildPrompt,
  exportCsv,
  exportJson,
  factorsOfCategory,
  numOf,
  radarData,
  requestLlmPreview,
  simulateOutput,
  tokenReport,
} from '../../lib/promptFactors';
import type { FactorCategory, FactorValue } from '../../lib/promptFactorTypes';
import FactorCard from './FactorCard';
import SystemPartCard from './SystemPartCard';
import AddSystemPartForm from './AddSystemPartForm';
import CustomFactorForm from './CustomFactorForm';
import PreviewPanel, { type PreviewMode } from './PreviewPanel';
import '../PromptLab.css';

type Filter = 'all' | FactorCategory;

const dateTag = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

export default function PromptLab() {
  const values = usePromptLabStore((s) => s.values);
  const customFactors = usePromptLabStore((s) => s.customFactors);
  const systemParts = usePromptLabStore((s) => s.systemParts);
  const llm = usePromptLabStore((s) => s.llm);
  const setValue = usePromptLabStore((s) => s.setValue);
  const removeCustomFactor = usePromptLabStore((s) => s.removeCustomFactor);
  const updateSystemPart = usePromptLabStore((s) => s.updateSystemPart);
  const removeSystemPart = usePromptLabStore((s) => s.removeSystemPart);
  const resetSystemParts = usePromptLabStore((s) => s.resetSystemParts);
  const resetValues = usePromptLabStore((s) => s.resetValues);

  const [filter, setFilter] = useState<Filter>('all');
  const [showForm, setShowForm] = useState(false);
  const [showPartForm, setShowPartForm] = useState(false);
  const [mode, setMode] = useState<PreviewMode>('sim');
  const [llmOutput, setLlmOutput] = useState<string | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);

  const defs = useMemo(() => allFactors(customFactors), [customFactors]);

  const prompt = useMemo(() => buildPrompt(values), [values]);
  const sim = useMemo(() => simulateOutput(values), [values]);
  const radar = useMemo(() => radarData(values), [values]);
  const token = useMemo(
    () => tokenReport(systemParts, values, llm.contextWindow),
    [systemParts, values, llm.contextWindow],
  );
  const systemPrompt = useMemo(() => assembleSystemPrompt(systemParts), [systemParts]);

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
      downloadFile(`prompt-factors-${tag}.json`, exportJson(values, customFactors, systemParts, llm.contextWindow), 'application/json');
    else downloadFile(`prompt-factors-${tag}.csv`, exportCsv(values, customFactors), 'text/csv;charset=utf-8');
  };

  return (
    <div className="pl-root">
      {/* 顶栏 */}
      <header className="pl-topbar">
        <div className="flex items-center gap-2.5">
          <button type="button" className="pl-btn ghost" onClick={() => { window.location.hash = '#/'; }} title="返回工作台">
            <ArrowLeft size={14} />
            返回工作台
          </button>
          <div className="pl-logo-badge">
            <FlaskConical size={15} />
          </div>
          <div>
            <h1 className="pl-title">提示词影响因素实验室</h1>
            <p className="pl-subtitle">系统化调节影响 LLM 提示词质量的 5 类因素，实时观察组合效果</p>
          </div>
        </div>
        <div className="pl-topbar-actions">
          <button type="button" className="pl-btn" onClick={() => setShowPartForm(true)} title="新增自定义系统提示词组件">
            <Plus size={13} />
            添加组件
          </button>
          <button type="button" className="pl-btn" onClick={() => setShowForm(true)}>
            <Plus size={13} />
            添加因素
          </button>
          <button type="button" className="pl-btn" onClick={resetValues} title="所有因素恢复默认值">
            <RotateCcw size={13} />
            重置
          </button>
        </div>
      </header>

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
        {/* 左侧：因素模块 */}
        <main className="pl-factors">
          {/* 系统提示词组件：注入每次 LLM 调用的 system message */}
          <section className="pl-category">
            <h2 className="pl-category-title" style={{ color: '#f472b6' }}>
              <span className="pl-category-dot" style={{ background: '#f472b6' }} />
              系统提示词组件
              <span className="pl-category-count">{systemParts.length} 段 · 共 ~{token.systemTotal} tok</span>
            </h2>
            <p className="pl-category-desc">
              每次真实 LLM 调用注入的 system message 由下列组件按顺序拼接，可逐段启停 / 编辑 / 新增，实时估算 token。
            </p>
            <div className="pl-card-grid">
              {systemParts.map((p) => (
                <SystemPartCard key={p.id} part={p} onUpdate={updateSystemPart} onRemove={removeSystemPart} />
              ))}
            </div>
            <div className="pl-syspart-actions">
              <button type="button" className="pl-btn" onClick={() => setShowPartForm(true)}>
                <Plus size={13} />
                添加组件
              </button>
              <button type="button" className="pl-btn" onClick={resetSystemParts} title="恢复默认 7 段组件">
                <RotateCcw size={13} />
                恢复默认组件
              </button>
            </div>
          </section>

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

      {showForm && <CustomFactorForm onClose={() => setShowForm(false)} />}
      {showPartForm && <AddSystemPartForm onClose={() => setShowPartForm(false)} />}
    </div>
  );
}
