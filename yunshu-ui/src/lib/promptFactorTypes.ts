/**
 * 提示词影响因素 · 共享类型
 * ------------------------------------------------
 * 与 promptFactors.ts 分离，供 store / 页面 / 单测共同引用，避免循环依赖。
 */

/** 五大因素分类 id（持久化锚点，勿随意改动） */
export type FactorCategory = 'structure' | 'language' | 'context' | 'model' | 'evaluation';

/** 控件类型：滑块 / 下拉 / 文本 / 开关 */
export type FactorControl = 'slider' | 'select' | 'text' | 'toggle';

/** 因素当前值：数值 / 枚举字符串 / 开关布尔 */
export type FactorValue = number | string | boolean;

export interface FactorOption {
  value: string;
  label: string;
}

/** 因素定义：描述"有什么因素、如何调节"（纯数据，可持久化） */
export interface PromptFactorDef {
  id: string;
  category: FactorCategory;
  name: string;
  desc: string;
  control: FactorControl;
  /** slider 范围与步进 */
  min?: number;
  max?: number;
  step?: number;
  /** 数值单位（如 分/轮/tok），为空则不显示 */
  unit?: string;
  /** select 选项 */
  options?: FactorOption[];
  /** text 占位符 */
  placeholder?: string;
  defaultValue: FactorValue;
  /** 是否为用户自定义因素 */
  custom?: boolean;
}
