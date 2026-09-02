/**
 * FactorCard —— 因素卡片（名称 + 说明 + 控件 + 自定义删除）
 * 从 PromptLab.tsx 拆分（原 169-196 行，逻辑不变）。
 */
import { Trash2 } from 'lucide-react';
import { CATEGORIES } from '../../lib/promptFactors';
import type { FactorValue, PromptFactorDef } from '../../lib/promptFactorTypes';
import FactorControl from './FactorControl';

interface FactorCardProps {
  def: PromptFactorDef;
  value: FactorValue;
  onChange: (v: FactorValue) => void;
  onRemove?: (id: string) => void;
}

export default function FactorCard({ def, value, onChange, onRemove }: FactorCardProps) {
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
