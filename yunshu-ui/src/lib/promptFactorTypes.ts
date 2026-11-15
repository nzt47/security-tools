/**
 * 提示词影响因素 · 共享类型
 * ------------------------------------------------
 * 与 promptFactors.ts 分离，供 store / 页面 / 单测共同引用，避免循环依赖。
 */

/**
 * 因素分类 id（持久化锚点，勿随意改动）。
 *
 * 【2026-11-15 加 'personality'】人格 6 维（语气/情感/简练/主动/幽默/同理心）并进
 * 「提示词影响因素」（§11.124 D-2 轻量版）：它和别的因素**不是同一种东西** ——
 * 其余分类只影响"这一份预览提示词"，人格这 6 个值会直接写后端 /api/personality，
 * 且只有系统提示词的 persona 节启用时才真的进提示词。故单列一个分类，而不是
 * 塞进 context/model。
 */
export type FactorCategory = 'structure' | 'language' | 'context' | 'model' | 'evaluation' | 'personality';

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
  /**
   * slider 左右端语义（可选）：如人格"语气"的「正式 / 随意」。
   * 0–1 这种无单位的滑块光看数字不知道两端意味着什么，故按后端
   * /api/personality 的 dimensions 原样标注（不改后端文案）。
   */
  leftLabel?: string;
  rightLabel?: string;
  /** select 选项 */
  options?: FactorOption[];
  /** text 占位符 */
  placeholder?: string;
  defaultValue: FactorValue;
  /** 是否为用户自定义因素 */
  custom?: boolean;
}
