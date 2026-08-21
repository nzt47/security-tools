/**
 * FactorControl —— 单个因素控件（滑块 / 下拉 / 文本 / 开关）
 * 从 PromptLab.tsx 拆分（原 96-166 行，逻辑不变）。
 */
import type { FactorValue, PromptFactorDef } from '../../lib/promptFactorTypes';

interface FactorControlProps {
  def: PromptFactorDef;
  value: FactorValue;
  onChange: (v: FactorValue) => void;
}

export default function FactorControl({ def, value, onChange }: FactorControlProps) {
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
