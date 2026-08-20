/**
 * PromptLab —— 提示词影响因素管理面板
 * ------------------------------------------------
 * 布局：顶部（返回/标题/分类筛选/添加因素/重置）+ 左侧因素模块 + 右侧粘性预览面板
 * 功能：5 类因素调节（滑块/下拉/文本/开关）、实时预览（模拟 + 预留真实 LLM）、
 *       分类筛选、自定义因素添加/删除、JSON/CSV 导出。
 * 路由：独立页面 #/prompt-lab（不进 Mosaic 布局，顶部导航进入）。
 */
import { useMemo, useState } from 'react';
import {
  ArrowLeft,
  Download,
  FlaskConical,
  Loader2,
  Plus,
  RotateCcw,
  Settings2,
  Trash2,
  Wrench,
} from 'lucide-react';
import { usePromptLabStore } from '../stores/usePromptLabStore';
import { downloadFile } from '../utils/system';
import {
  CATEGORIES,
  allFactors,
  assembleSystemPrompt,
  buildPrompt,
  estimateTokens,
  exportCsv,
  exportJson,
  factorsOfCategory,
  numOf,
  radarData,
  requestLlmPreview,
  simulateOutput,
  tokenReport,
} from '../lib/promptFactors';
import type {
  FactorCategory,
  FactorControl,
  FactorValue,
  PromptFactorDef,
  SystemPart,
} from '../lib/promptFactorTypes';
import './PromptLab.css';

type Filter = 'all' | FactorCategory;
type PreviewMode = 'sim' | 'llm';

const dateTag = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

/** 雷达图：SVG 五维多边形（不引入图表库） */
function RadarChart({ data }: { data: { label: string; value: number }[] }) {
  const size = 220;
  const cx = size / 2;
  const cy = size / 2;
  const r = 74;
  const n = data.length;
  const point = (i: number, scale: number): [number, number] => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [cx + Math.cos(angle) * r * scale, cy + Math.sin(angle) * r * scale];
  };
  const poly = (scale: number) =>
    data.map((_, i) => point(i, scale).map((v) => v.toFixed(1)).join(',')).join(' ');
  const valuePoly = data.map((d, i) => point(i, Math.max(0.03, d.value / 100)).join(',')).join(' ');

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="pl-radar" role="img" aria-label="效果评估雷达图">
      {[0.25, 0.5, 0.75, 1].map((s) => (
        <polygon key={s} points={poly(s)} fill="none" stroke="rgba(148,163,184,0.25)" strokeWidth={1} />
      ))}
      {data.map((_, i) => {
        const [x, y] = point(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(148,163,184,0.18)" strokeWidth={1} />;
      })}
      <polygon points={valuePoly} fill="rgba(34,211,238,0.25)" stroke="#22d3ee" strokeWidth={2} />
      {data.map((d, i) => {
        const [x, y] = point(i, d.value / 100);
        return (
          <g key={d.label}>
            <circle cx={x} cy={y} r={3.5} fill="#22d3ee" />
            <text x={point(i, 1.18)[0]} y={point(i, 1.18)[1]} textAnchor="middle" dominantBaseline="middle" className="pl-radar-label">
              {d.label}
            </text>
            <text x={point(i, 0.88)[0]} y={point(i, 0.88)[1]} textAnchor="middle" dominantBaseline="middle" className="pl-radar-value">
              {d.value}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** 单个因素控件（滑块/下拉/文本/开关） */
function FactorControl({
  def,
  value,
  onChange,
}: {
  def: PromptFactorDef;
  value: FactorValue;
  onChange: (v: FactorValue) => void;
}) {
  if (def.control === 'slider') {
    const min = def.min ?? 0;
    const max = def.max ?? 100;
    const step = def.step ?? 1;
    return (
      <div className="pl-slider-row">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={typeof value === 'number' ? value : Number(def.defaultValue)}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={def.name}
        />
        <span className="pl-slider-val">
          {typeof value === 'number' ? value : def.defaultValue}
          {def.unit ? ` ${def.unit}` : ''}
        </span>
      </div>
    );
  }
  if (def.control === 'select') {
    return (
      <select
        className="pl-select"
        value={typeof value === 'string' ? value : String(def.defaultValue)}
        onChange={(e) => onChange(e.target.value)}
        aria-label={def.name}
      >
        {(def.options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (def.control === 'text') {
    return (
      <input
        type="text"
        className="pl-text"
        value={typeof value === 'string' ? value : String(def.defaultValue)}
        placeholder={def.placeholder ?? '输入…'}
        onChange={(e) => onChange(e.target.value)}
        aria-label={def.name}
      />
    );
  }
  return (
    <button
      type="button"
      className={`pl-toggle ${value === true ? 'on' : ''}`}
      onClick={() => onChange(value !== true)}
      aria-pressed={value === true}
    >
      <span className="pl-toggle-dot" />
      {value === true ? '开启' : '关闭'}
    </button>
  );
}

/** 因素卡片：名称 + 说明 + 控件 + （自定义）删除 */
function FactorCard({
  def,
  value,
  onChange,
  onRemove,
}: {
  def: PromptFactorDef;
  value: FactorValue;
  onChange: (v: FactorValue) => void;
  onRemove?: (id: string) => void;
}) {
  const color = CATEGORIES.find((c) => c.id === def.category)?.color ?? '#22d3ee';
  return (
    <div className="pl-factor-card">
      <div className="pl-factor-head">
        <span className="pl-factor-name">{def.name}</span>
        {def.custom && (
          <button type="button" className="pl-icon-btn" onClick={() => onRemove?.(def.id)} title="删除此自定义因素">
            <Trash2 size={13} />
          </button>
        )}
      </div>
      <p className="pl-factor-desc">{def.desc}</p>
      <FactorControl def={def} value={value} onChange={onChange} />
      {def.control === 'slider' && <span className="pl-factor-bar" style={{ width: `${((typeof value === 'number' ? value : Number(def.defaultValue)) / ((def.max ?? 100) - (def.min ?? 0))) * 100}%`, background: color }} />}
    </div>
  );
}

/** 系统提示词组件卡片：启停 + 文本编辑 + token 估算（内置不可删，自定义可删） */
function SystemPartCard({
  part,
  onUpdate,
  onRemove,
}: {
  part: SystemPart;
  onUpdate: (id: string, patch: Partial<SystemPart>) => void;
  onRemove?: (id: string) => void;
}) {
  return (
    <div className="pl-factor-card pl-syspart">
      <div className="pl-factor-head">
        <span className="pl-factor-name">{part.label}</span>
        <div className="flex items-center gap-2">
          <span className="pl-token-chip" title="该组件估算 token 数">
            ~{estimateTokens(part.text)} tok
          </span>
          <button
            type="button"
            className={`pl-toggle ${part.enabled ? 'on' : ''}`}
            onClick={() => onUpdate(part.id, { enabled: !part.enabled })}
            aria-pressed={part.enabled}
          >
            <span className="pl-toggle-dot" />
            {part.enabled ? '注入' : '禁用'}
          </button>
          {!part.builtin && (
            <button type="button" className="pl-icon-btn" onClick={() => onRemove?.(part.id)} title="删除此自定义组件">
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
      <textarea
        className={`pl-textarea pl-syspart-text ${part.enabled ? '' : 'disabled'}`}
        rows={2}
        value={part.text}
        disabled={!part.enabled}
        onChange={(e) => onUpdate(part.id, { text: e.target.value })}
        spellCheck={false}
      />
    </div>
  );
}

/** 添加自定义系统提示词组件的弹窗 */
function AddSystemPartForm({ onClose }: { onClose: () => void }) {
  const addSystemPart = usePromptLabStore((s) => s.addSystemPart);
  const [label, setLabel] = useState('');
  const [text, setText] = useState('');

  const submit = () => {
    if (!label.trim() || !text.trim()) return;
    addSystemPart({
      id: `sp-custom-${Date.now()}`,
      label: label.trim(),
      enabled: true,
      text: text.trim(),
    });
    onClose();
  };

  return (
    <div className="pl-modal-mask" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="pl-modal-title">添加系统提示词组件</h3>
        <label className="pl-field">
          <span>组件名称 *</span>
          <input className="pl-text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="例如：知识库检索规则" />
        </label>
        <label className="pl-field">
          <span>组件文本 *（注入 system message 的一段）</span>
          <textarea className="pl-textarea" value={text} onChange={(e) => setText(e.target.value)} rows={4} placeholder="输入要注入的内容…" />
        </label>
        <div className="pl-modal-actions">
          <button type="button" className="pl-btn ghost" onClick={onClose}>取消</button>
          <button type="button" className="pl-btn primary" onClick={submit} disabled={!label.trim() || !text.trim()}>
            添加
          </button>
        </div>
      </div>
    </div>
  );
}

/** 添加自定义因素的表单弹层 */
function CustomFactorForm({ onClose }: { onClose: () => void }) {
  const addCustomFactor = usePromptLabStore((s) => s.addCustomFactor);
  const [name, setName] = useState('');
  const [category, setCategory] = useState<FactorCategory>('structure');
  const [control, setControl] = useState<FactorControl>('slider');
  const [min, setMin] = useState('0');
  const [max, setMax] = useState('100');
  const [step, setStep] = useState('1');
  const [unit, setUnit] = useState('');
  const [optionsText, setOptionsText] = useState('low:低\nmedium:中\nhigh:高');
  const [placeholder, setPlaceholder] = useState('');
  const [defaultVal, setDefaultVal] = useState('50');
  const [desc, setDesc] = useState('');

  const submit = () => {
    if (!name.trim()) return;
    const def: PromptFactorDef = {
      id: `custom-${Date.now()}`,
      category,
      name: name.trim(),
      desc: desc.trim() || '用户自定义因素',
      control,
      defaultValue: control === 'toggle' ? defaultVal === 'true' : control === 'select' ? optionsText.split('\n').find(Boolean)?.split(':')[0] ?? 'low' : Number(defaultVal) || 0,
    };
    if (control === 'slider') {
      def.min = Number(min) || 0;
      def.max = Number(max) || 100;
      def.step = Number(step) || 1;
      if (unit) def.unit = unit;
    }
    if (control === 'select') {
      def.options = optionsText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [value, ...rest] = line.split(':');
          return { value: value.trim(), label: rest.join(':').trim() || value.trim() };
        });
    }
    if (control === 'text' && placeholder) def.placeholder = placeholder;
    addCustomFactor(def);
    onClose();
  };

  return (
    <div className="pl-modal-mask" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="pl-modal-title">添加自定义因素</h3>
        <label className="pl-field">
          <span>名称 *</span>
          <input className="pl-text" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：输出语言风格" />
        </label>
        <div className="pl-modal-grid">
          <label className="pl-field">
            <span>分类</span>
            <select className="pl-select" value={category} onChange={(e) => setCategory(e.target.value as FactorCategory)}>
              {CATEGORIES.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </label>
          <label className="pl-field">
            <span>控件类型</span>
            <select className="pl-select" value={control} onChange={(e) => setControl(e.target.value as FactorControl)}>
              <option value="slider">滑块</option>
              <option value="select">下拉</option>
              <option value="text">文本输入</option>
              <option value="toggle">开关</option>
            </select>
          </label>
        </div>
        {control === 'slider' && (
          <div className="pl-modal-grid">
            <label className="pl-field"><span>最小值</span><input className="pl-text" value={min} onChange={(e) => setMin(e.target.value)} /></label>
            <label className="pl-field"><span>最大值</span><input className="pl-text" value={max} onChange={(e) => setMax(e.target.value)} /></label>
            <label className="pl-field"><span>步进</span><input className="pl-text" value={step} onChange={(e) => setStep(e.target.value)} /></label>
            <label className="pl-field"><span>单位</span><input className="pl-text" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="如 分/轮" /></label>
          </div>
        )}
        {control === 'select' && (
          <label className="pl-field">
            <span>选项（每行 值:标签）</span>
            <textarea className="pl-textarea" value={optionsText} onChange={(e) => setOptionsText(e.target.value)} rows={4} />
          </label>
        )}
        {control === 'text' && (
          <label className="pl-field">
            <span>占位符</span>
            <input className="pl-text" value={placeholder} onChange={(e) => setPlaceholder(e.target.value)} />
          </label>
        )}
        <label className="pl-field">
          <span>默认值{control === 'toggle' ? '（true/false）' : ''}</span>
          {control === 'toggle' ? (
            <select className="pl-select" value={defaultVal} onChange={(e) => setDefaultVal(e.target.value)}>
              <option value="true">开启</option>
              <option value="false">关闭</option>
            </select>
          ) : (
            <input className="pl-text" value={defaultVal} onChange={(e) => setDefaultVal(e.target.value)} />
          )}
        </label>
        <label className="pl-field">
          <span>说明</span>
          <textarea className="pl-textarea" value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} placeholder="该因素影响什么？" />
        </label>
        <div className="pl-modal-actions">
          <button type="button" className="pl-btn ghost" onClick={onClose}>取消</button>
          <button type="button" className="pl-btn primary" onClick={submit} disabled={!name.trim()}>添加</button>
        </div>
      </div>
    </div>
  );
}

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
  const setLlm = usePromptLabStore((s) => s.setLlm);

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

        {/* 右侧：实时预览面板 */}
        <aside className="pl-preview">
          <div className="pl-preview-head">
            <h2>实时预览</h2>
            <div className="pl-mode-switch" role="tablist" aria-label="预览模式">
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'sim'}
                className={mode === 'sim' ? 'active' : ''}
                onClick={() => { setMode('sim'); setLlmError(null); }}
              >
                模拟
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'llm'}
                className={mode === 'llm' ? 'active' : ''}
                onClick={() => { setMode('llm'); setLlmError(null); }}
              >
                真实接口
              </button>
            </div>
          </div>

          {mode === 'llm' && (
            <details className="pl-llm-config" open>
              <summary>
                <Settings2 size={12} /> LLM 接口配置（OpenAI 兼容）
              </summary>
              <label className="pl-field">
                <span>Endpoint</span>
                <input className="pl-text" value={llm.endpoint} onChange={(e) => setLlm({ endpoint: e.target.value })} placeholder="https://api.openai.com/v1/chat/completions" />
              </label>
              <label className="pl-field">
                <span>API Key</span>
                <input className="pl-text" type="password" value={llm.apiKey} onChange={(e) => setLlm({ apiKey: e.target.value })} placeholder="sk-…（仅本地存储）" />
              </label>
              <label className="pl-field">
                <span>模型</span>
                <input className="pl-text" value={llm.model} onChange={(e) => setLlm({ model: e.target.value })} />
              </label>
            </details>
          )}

          <div className="pl-preview-block">
            <h3>系统提示词（注入）</h3>
            <pre className="pl-sim-out">{systemPrompt}</pre>
          </div>

          <div className="pl-preview-block">
            <h3>生成的提示词</h3>
            <textarea className="pl-prompt-out" readOnly value={prompt} rows={9} spellCheck={false} />
          </div>

          <div className="pl-preview-block">
            <h3>{mode === 'sim' ? '模拟输出' : '真实输出'}</h3>
            {mode === 'llm' ? (
              <>
                <div className="pl-preview-actions">
                  <button type="button" className="pl-btn primary" onClick={runLlm} disabled={llmLoading}>
                    {llmLoading ? <Loader2 size={13} className="spin" /> : <Wrench size={13} />}
                    {llmLoading ? '请求中…' : '请求真实输出'}
                  </button>
                </div>
                {llmError && <p className="pl-error">{llmError}</p>}
                <pre className="pl-sim-out">{llmOutput ?? '尚未请求。参数变化后请重新点击"请求真实输出"。'}</pre>
              </>
            ) : (
              <pre className="pl-sim-out">{sim}</pre>
            )}
          </div>

          <div className="pl-preview-block">
            <h3>效果评估</h3>
            <RadarChart data={radar} />
          </div>

          <div className="pl-preview-block">
            <h3>Token 用量估算</h3>
            <div className="pl-token-table">
              {token.rows.map((r) => (
                <div key={r.id} className={`pl-token-row ${r.enabled ? '' : 'off'}`}>
                  <span>{r.label}</span>
                  <span>{r.enabled ? `~${r.tokens} tok` : '—'}</span>
                </div>
              ))}
              <div className="pl-token-row total">
                <span>系统提示词合计</span>
                <span>~{token.systemTotal} tok</span>
              </div>
              <div className="pl-token-row">
                <span>生成的提示词</span>
                <span>~{token.userTokens} tok</span>
              </div>
              <div className="pl-token-row total">
                <span>总计</span>
                <span>~{token.total} tok</span>
              </div>
            </div>
            <div className="pl-token-bar">
              <div
                className="pl-token-bar-fill"
                style={{ width: `${Math.max(2, token.usedPct)}%`, background: token.usedPct > 80 ? '#f87171' : '#22d3ee' }}
              />
            </div>
            <div className="pl-token-meta">
              <span>
                占用上下文 {token.usedPct}% / 窗口 {token.windowSize.toLocaleString()}
              </span>
              <label className="pl-token-window-label">
                窗口
                <input
                  type="number"
                  className="pl-text pl-token-window"
                  value={llm.contextWindow}
                  min={1024}
                  step={1024}
                  onChange={(e) => setLlm({ contextWindow: Math.max(1024, Number(e.target.value) || 32768) })}
                />
              </label>
            </div>
          </div>

          <div className="pl-export">
            <h3>数据导出</h3>
            <p className="pl-export-hint">导出当前参数组合，便于提示词优化分析（JSON 可再导入，CSV 用于表格处理）。</p>
            <div className="pl-export-actions">
              <button type="button" className="pl-btn" onClick={() => exportWith('json')}>
                <Download size={13} /> 导出 JSON
              </button>
              <button type="button" className="pl-btn" onClick={() => exportWith('csv')}>
                <Download size={13} /> 导出 CSV
              </button>
            </div>
          </div>
        </aside>
      </div>

      {showForm && <CustomFactorForm onClose={() => setShowForm(false)} />}
      {showPartForm && <AddSystemPartForm onClose={() => setShowPartForm(false)} />}
    </div>
  );
}
