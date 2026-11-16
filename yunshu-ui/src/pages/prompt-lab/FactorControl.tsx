/**
 * FactorControl —— 单个因素控件（滑块 / 下拉 / 文本 / 开关）
 * 从 PromptLab.tsx 拆分（原 96-166 行，逻辑不变）。
 * 【2026-11-16 统一】四类控件一律用 components/ui 的原语（Slider / Select / Input / Switch）：
 *   原来这里的滑块是手写 input[type=range] + 页面 CSS，开关是带「开启/关闭」文字的 pill
 *   按钮（= 额外的状态列）；现在开关只表达状态：开=亮、关=灰，不再有文字。
 */
import { Input, Select, Slider, Switch } from '@/components/ui'
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
      <>
        <div className="pl-slider-row">
          <Slider
            className="min-w-0 flex-1"
            min={min}
            max={max}
            step={step}
            value={typeof value === 'number' ? value : Number(def.defaultValue)}
            onValueChange={onChange}
            aria-label={def.name}
          />
          <span className="pl-slider-val">
            {typeof value === 'number' ? value : def.defaultValue}
            {def.unit ? ` ${def.unit}` : ''}
          </span>
        </div>
        {/* 左右端语义（2026-11-15 人格 6 维引入）：0–1 这种没有单位的滑块，
            光看数字不知道两端是什么，故按后端 dimensions 的 left/right 标注。 */}
        {(def.leftLabel || def.rightLabel) && (
          <div className="pl-slider-edges">
            <span>{def.leftLabel ?? ''}</span>
            <span>{def.rightLabel ?? ''}</span>
          </div>
        )}
      </>
    );
  }
  if (def.control === 'select') {
    return (
      <Select
        className="pl-select-wrap"
        options={def.options ?? []}
        value={typeof value === 'string' ? value : String(def.defaultValue)}
        onChange={onChange}
        placeholder={def.placeholder ?? '请选择'}
        aria-label={def.name}
      />
    );
  }
  if (def.control === 'text') {
    return (
      <Input
        className="pl-text-wrap"
        type="text"
        value={typeof value === 'string' ? value : String(def.defaultValue)}
        placeholder={def.placeholder ?? '输入…'}
        onChange={(e) => onChange(e.target.value)}
        aria-label={def.name}
      />
    );
  }
  // boolean：开关即状态（无「开启/关闭」文字列），开=亮 / 关=灰
  return (
    <Switch
      checked={value === true}
      onCheckedChange={(next) => onChange(next)}
      label={def.name}
      size="sm"
    />
  );
}
