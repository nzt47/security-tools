/**
 * CustomFactorForm —— 添加自定义因素表单弹层
 * 从 PromptLab.tsx 拆分（原 285-399 行，逻辑不变）。
 */
import { useState } from 'react';
import { Button, Input, Select, Textarea } from '@/components/ui';
import { usePromptLabStore } from '../../stores/usePromptLabStore';
import { CATEGORIES } from '../../lib/promptFactors';
import type { FactorCategory, FactorControl, PromptFactorDef } from '../../lib/promptFactorTypes';

interface CustomFactorFormProps {
  onClose: () => void;
}

export default function CustomFactorForm({ onClose }: CustomFactorFormProps) {
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
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：输出语言风格" size="sm" />
        </label>
        <div className="pl-modal-grid">
          <label className="pl-field">
            <span>分类</span>
            <Select
              value={category}
              onChange={(v) => setCategory(v as FactorCategory)}
              size="sm"
              hidePlaceholder
              options={CATEGORIES.map((c) => ({ label: c.label, value: c.id }))}
            />
          </label>
          <label className="pl-field">
            <span>控件类型</span>
            <Select value={control} onChange={(v) => setControl(v as FactorControl)} size="sm" hidePlaceholder options={[{ label: '滑块', value: 'slider' }, { label: '下拉', value: 'select' }, { label: '文本输入', value: 'text' }, { label: '开关', value: 'toggle' }]} />
          </label>
        </div>
        {control === 'slider' && (
          <div className="pl-modal-grid">
            <label className="pl-field"><span>最小值</span><Input value={min} onChange={(e) => setMin(e.target.value)} size="sm" /></label>
            <label className="pl-field"><span>最大值</span><Input value={max} onChange={(e) => setMax(e.target.value)} size="sm" /></label>
            <label className="pl-field"><span>步进</span><Input value={step} onChange={(e) => setStep(e.target.value)} size="sm" /></label>
            <label className="pl-field"><span>单位</span><Input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="如 分/轮" size="sm" /></label>
          </div>
        )}
        {control === 'select' && (
          <label className="pl-field">
            <span>选项（每行 值:标签）</span>
            <Textarea size="sm" value={optionsText} onChange={(e) => setOptionsText(e.target.value)} rows={4} />
          </label>
        )}
        {control === 'text' && (
          <label className="pl-field">
            <span>占位符</span>
            <Input value={placeholder} onChange={(e) => setPlaceholder(e.target.value)} size="sm" />
          </label>
        )}
        <label className="pl-field">
          <span>默认值{control === 'toggle' ? '（true/false）' : ''}</span>
          {control === 'toggle' ? (
            <Select value={defaultVal} onChange={(v) => setDefaultVal(v)} size="sm" hidePlaceholder options={[{ label: '开启', value: 'true' }, { label: '关闭', value: 'false' }]} />
          ) : (
            <Input value={defaultVal} onChange={(e) => setDefaultVal(e.target.value)} size="sm" />
          )}
        </label>
        <label className="pl-field">
          <span>说明</span>
          <Textarea size="sm" value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} placeholder="该因素影响什么？" />
        </label>
        <div className="pl-modal-actions">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>取消</Button>
          <Button type="button" variant="primary" size="sm" onClick={submit} disabled={!name.trim()}>添加</Button>
        </div>
      </div>
    </div>
  );
}
