/**
 * 提示词影响因素 · 数据模型 / 默认因素 / 提示词构建与 LLM 试跑
 * ------------------------------------------------
 * 设计约束（不易）：
 *  - 因素定义（def）与"当前值"分离：def 描述"有什么因素、怎么调节"，
 *    当前值由 usePromptLabStore 持久化。默认因素不可改，仅可调值。
 *  - 因素 id 是持久化锚点（LocalStorage 键值），改名/删号即丢配置。
 *  - 所有纯数据，无 React 依赖，便于单测与导出。
 *
 * 深度合并说明（系统提示词不再本地沙箱化）：
 *  - 提示词实验室注入预览/真实输出的 system message = 后端「身份提示词」
 *    配置生成的模板（见 identityPrompt.ts），本地不再维护独立 7 段组件。
 *  - 因此本文件只保留因素引擎与 token 估算；模板文本由上游传入。
 */
import type { FactorCategory, PromptFactorDef, FactorValue } from './promptFactorTypes';
import { postEnvelope } from '@/api/envelope';
import { PROMPT_LAB_LLM_PREVIEW } from '@/api/endpoints';

export type { FactorCategory, PromptFactorDef, FactorValue };

/** 五大分类元数据：id 为锚点，label 用于界面展示 */
export const CATEGORIES: { id: FactorCategory; label: string; short: string; desc: string; color: string }[] = [
  { id: 'structure', label: '提示词结构要素', short: '结构', desc: '提示词的骨架与组织方式', color: '#22d3ee' },
  { id: 'language', label: '语言表达特征', short: '语言', desc: '措辞、句式与术语的风格', color: '#a78bfa' },
  { id: 'context', label: '上下文参数控制', short: '上下文', desc: '对话历史、角色与背景注入', color: '#fbbf24' },
  { id: 'model', label: '模型参数调节', short: '模型', desc: '采样与输出长度等生成控制', color: '#34d399' },
  { id: 'evaluation', label: '效果评估指标', short: '评估', desc: '对输出质量的多维量化评估', color: '#f87171' },
  // 【2026-11-15】人格并进因素（§11.124 D-2 轻量版）：6 个 0–1 滑块直接写
  // POST /api/personality/params（防抖 400ms）。与其它分类的区别写在 desc 里：
  // 它不是"这一份预览提示词"的参数，而是后端的人格档位，只有 persona 节启用才进提示词。
  {
    id: 'personality', label: '人格（云枢的语气体征）', short: '人格',
    desc: '这 6 维直接写后端 /api/personality（不是本页预览参数）；只有系统提示词的 persona 节启用时，它们才会被渲染进提示词',
    color: '#e879f9',
  },
];

/**
 * 人格 6 维 —— 后端 /api/personality 的 `dimensions` 原样搬来（key/label/left/right 逐字对齐，
 * 默认值取后端 `_default().custom_params`）。这里是「因素 id ↔ 后端键」的**唯一一份**映射：
 * 界面文案、默认值、以及 personalityParamsOf() 的收发都用它，避免两处各写一遍走样。
 */
export const PERSONALITY_DIMENSIONS: {
  key: string;
  label: string;
  left: string;
  right: string;
  defaultValue: number;
  desc: string;
}[] = [
  { key: 'tone', label: '语气', left: '正式', right: '随意', defaultValue: 0.6, desc: '措辞的正式程度。左端拘谨克制，右端松弛口语。' },
  { key: 'emotion', label: '情感', left: '克制', right: '丰富', defaultValue: 0.7, desc: '情绪表达的多寡。左端只讲事实，右端会表达感受。' },
  { key: 'conciseness', label: '简练', left: '详细', right: '简洁', defaultValue: 0.4, desc: '回答的详略。左端展开推演，右端先给结论。' },
  { key: 'initiative', label: '主动', left: '被动', right: '主动', defaultValue: 0.5, desc: '是否主动补充/推进。左端问什么答什么，右端会接着追问。' },
  { key: 'humor', label: '幽默', left: '严肃', right: '幽默', defaultValue: 0.3, desc: '开玩笑的密度。左端一本正经，右端会玩梗。' },
  { key: 'empathy', label: '同理心', left: '理性', right: '感性', defaultValue: 0.8, desc: '回应的落脚点。左端先分析，右端先接住情绪。' },
];

/** 人格维度的因素 id（`personality.<后端键>`）；持久化锚点，勿改 */
export const PERSONALITY_FACTOR_IDS: string[] = PERSONALITY_DIMENSIONS.map((d) => 'personality.' + d.key);

/** 人格 6 维的因素定义（category='personality'） */
const PERSONALITY_FACTORS: PromptFactorDef[] = PERSONALITY_DIMENSIONS.map((d) => ({
  id: 'personality.' + d.key,
  category: 'personality',
  name: d.label,
  desc: d.desc,
  control: 'slider',
  min: 0,
  max: 1,
  step: 0.01,
  unit: '',
  leftLabel: d.left,
  rightLabel: d.right,
  defaultValue: d.defaultValue,
}));

/** 默认因素（唯一 id，值为锚点，勿随意修改 id） */
export const DEFAULT_FACTORS: PromptFactorDef[] = [
  // ── 1. 提示词结构要素 ──
  {
    id: 'clarity', category: 'structure', name: '指令明确性', control: 'slider',
    desc: '指令措辞是否明确、无歧义。分数越高，模型越不容易偏离任务意图。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 70,
  },
  {
    id: 'context_completeness', category: 'structure', name: '上下文完整性', control: 'slider',
    desc: '提示词是否包含完成任务所需的全部背景。缺失信息会导致模型猜测补全。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 60,
  },
  {
    id: 'task_clarity', category: 'structure', name: '任务描述清晰度', control: 'slider',
    desc: '输出目标、边界与验收标准的清晰程度。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 75,
  },
  // ── 2. 语言表达特征 ──
  {
    id: 'word_precision', category: 'language', name: '用词精准度', control: 'slider',
    desc: '词汇是否精确传达语义。精准的用词可减少模型的字面理解偏差。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 65,
  },
  {
    id: 'sentence_complexity', category: 'language', name: '句式复杂度', control: 'slider',
    desc: '低分=短句直白易读；高分=复杂从句，信息密度高但可读性下降。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 50,
  },
  {
    id: 'terminology', category: 'language', name: '专业术语使用', control: 'select',
    desc: '提示词中专业术语的密度，影响目标领域的专业度与可读性。',
    options: [
      { value: 'none', label: '不使用（通俗）' },
      { value: 'moderate', label: '适度（混合）' },
      { value: 'heavy', label: '大量（专业）' },
    ],
    defaultValue: 'moderate',
  },
  // ── 3. 上下文参数控制 ──
  {
    id: 'history_len', category: 'context', name: '历史对话长度', control: 'slider',
    desc: '携带前几轮对话作为上下文。过长会稀释注意力，过短则丢失线索。',
    min: 0, max: 50, step: 1, unit: '轮', defaultValue: 10,
  },
  {
    id: 'role_setting', category: 'context', name: '角色设定', control: 'text',
    desc: '为模型设定扮演的身份，影响语气、立场与知识侧重。',
    placeholder: '例如：你是一位资深技术顾问', defaultValue: '你是一位资深技术顾问',
  },
  {
    id: 'background_inject', category: 'context', name: '背景信息注入', control: 'toggle',
    desc: '是否在提示词开头注入项目背景说明（如领域、约束、目标读者）。',
    defaultValue: true,
  },
  // ── 4. 模型参数调节 ──
  {
    id: 'temperature', category: 'model', name: '温度系数', control: 'slider',
    desc: '采样随机性。越低越确定保守，越高越发散创新。',
    min: 0, max: 2, step: 0.1, unit: '', defaultValue: 0.7,
  },
  {
    id: 'top_p', category: 'model', name: 'Top-P', control: 'slider',
    desc: '核采样：仅从累计概率达 P 的最小词集采样。越小输出越聚焦。',
    min: 0, max: 1, step: 0.05, unit: '', defaultValue: 0.9,
  },
  {
    id: 'max_tokens', category: 'model', name: '最大输出长度', control: 'slider',
    desc: '单次生成的最大 token 上限，超出会被截断。',
    min: 512, max: 8192, step: 256, unit: 'tok', defaultValue: 2048,
  },
  // ── 5. 效果评估指标 ──
  {
    id: 'relevance', category: 'evaluation', name: '相关性评分', control: 'slider',
    desc: '输出与用户诉求的匹配程度（0-100，越高越好）。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 80,
  },
  {
    id: 'creativity', category: 'evaluation', name: '创造性指数', control: 'slider',
    desc: '输出的新颖与发散程度，与温度/Top-P 强相关。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 60,
  },
  {
    id: 'accuracy', category: 'evaluation', name: '事实准确性', control: 'slider',
    desc: '输出中事实信息的可靠程度，受上下文完整性影响。',
    min: 0, max: 100, step: 1, unit: '分', defaultValue: 85,
  },
  // ── 6. 人格（后端 /api/personality，定义见上方 PERSONALITY_DIMENSIONS） ──
  ...PERSONALITY_FACTORS,
];

/** 默认 id → 默认值映射（重置用） */
export const DEFAULT_VALUES: Record<string, FactorValue> = Object.fromEntries(
  DEFAULT_FACTORS.map((f) => [f.id, f.defaultValue]),
);

/** 0–1 夹紧：后端只接受 0–1，越界只可能来自脏 LocalStorage */
const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

/**
 * 从因素值取出人格 6 维参数（`{tone, emotion, conciseness, initiative, humor, empathy}`）。
 * 键名就是后端 dimensions 的 key，可直接作为 POST /api/personality/params 的 params。
 */
export function personalityParamsOf(values: Record<string, FactorValue>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const d of PERSONALITY_DIMENSIONS) out[d.key] = clamp01(numOf(values, 'personality.' + d.key));
  return out;
}

/**
 * 只挑出**真正变了**的键。
 *
 * 【为什么不是整份 6 键提交】后端 update_params() 是 `custom_params.update(params)` 的
 * 合并语义，所以只发改动项就够了。整份提交会在一个真实场景里写坏数据：用户在「人格」
 * Tab 里改了 A，再回到实验室拖 B —— 实验室内存里的 A 还是旧值，整份提交会把 Tab 里的
 * 改动悄悄覆盖回去。只发改动项，最坏也只影响正在拖的那一维。
 */
export function personalityParamsDiff(
  prev: Record<string, number>,
  next: Record<string, number>,
): Record<string, number> {
  const diff: Record<string, number> = {};
  for (const d of PERSONALITY_DIMENSIONS) {
    if (prev[d.key] !== next[d.key]) diff[d.key] = next[d.key];
  }
  return diff;
}

/** 取值辅助：从 values 取 id 的数值，非法时回退默认 */
export function numOf(values: Record<string, FactorValue>, id: string): number {
  const v = values[id] ?? DEFAULT_VALUES[id];
  return typeof v === 'number' ? v : Number(DEFAULT_VALUES[id] ?? 0);
}

/** 取值辅助：字符串 */
export function strOf(values: Record<string, FactorValue>, id: string): string {
  const v = values[id] ?? DEFAULT_VALUES[id];
  return typeof v === 'string' ? v : String(DEFAULT_VALUES[id] ?? '');
}

/** 取值辅助：布尔 */
export function boolOf(values: Record<string, FactorValue>, id: string): boolean {
  const v = values[id] ?? DEFAULT_VALUES[id];
  return typeof v === 'boolean' ? v : Boolean(DEFAULT_VALUES[id]);
}

/**
 * Token 估算（近似，非精确）：CJK 字符约 0.7 token/字，其余按 4 字符/token。
 * 用于实时观察各组件与总提示词的体积，实际消耗以模型计数为准。
 */
export function estimateTokens(text: string): number {
  const cjk = (text.match(/[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/g) ?? []).length;
  const other = text.length - cjk;
  return Math.max(1, Math.ceil(cjk * 0.7 + other / 4));
}

/**
 * Token 用量报告（注入 system 文本 + 用户提示词 + 上下文窗口占用）。
 * 输入文本为已知明文（后端模板 / 生成的提示词），直接估算，不做逐节拆分。
 */
export function usageReport(
  systemText: string,
  userText: string,
  contextWindow: number,
): {
  systemTotal: number;
  userTokens: number;
  total: number;
  windowSize: number;
  usedPct: number;
} {
  const systemTotal = estimateTokens(systemText);
  const userTokens = estimateTokens(userText);
  const total = systemTotal + userTokens;
  const usedPct = Math.min(100, Math.round((total / Math.max(1, contextWindow)) * 100));
  return { systemTotal, userTokens, total, windowSize: contextWindow, usedPct };
}

/** 分类 → 该分类下全部因素（默认 + 自定义） */
export function factorsOfCategory(defs: PromptFactorDef[], category: FactorCategory): PromptFactorDef[] {
  return defs.filter((f) => f.category === category);
}

/** 全部因素（默认 + 自定义） */
export function allFactors(customFactors: PromptFactorDef[]): PromptFactorDef[] {
  return [...DEFAULT_FACTORS, ...customFactors];
}

/**
 * 由当前因素值构建一份可复制的提示词文本（模拟预览 & 导出的核心产物）。
 */
export function buildPrompt(values: Record<string, FactorValue>): string {
  const lines: string[] = [];
  const role = strOf(values, 'role_setting');
  const temp = numOf(values, 'temperature');
  const topP = numOf(values, 'top_p');
  const maxTok = numOf(values, 'max_tokens');
  const history = numOf(values, 'history_len');

  lines.push(`# 提示词模板（由影响因素面板生成）`);
  if (role) lines.push(`## 角色\n${role}`);
  if (boolOf(values, 'background_inject')) {
    lines.push(`## 背景\n请基于本项目的技术背景回答，聚焦可落地的建议。`);
  }
  const historyDesc = history > 0 ? `携带最近 ${history} 轮对话作为上下文。` : `不携带历史对话（单轮问答）。`;
  lines.push(`## 上下文\n${historyDesc}`);
  lines.push(
    `## 任务\n请完成用户提出的任务，并遵循以下输出约束：\n` +
    `- 指令明确度 ${numOf(values, 'clarity')}/100 · 任务清晰度 ${numOf(values, 'task_clarity')}/100\n` +
    `- 用词精准度 ${numOf(values, 'word_precision')}/100 · 句式复杂度 ${numOf(values, 'sentence_complexity')}/100`,
  );
  lines.push(
    `## 生成参数\ntemperature=${temp.toFixed(1)} · top_p=${topP.toFixed(2)} · max_tokens=${maxTok}`,
  );
  return lines.join('\n\n');
}


/** 雷达图五维数据：评估类 3 维取自用户设置，连贯性/完整性由结构与语言因素推导 */
export function radarData(values: Record<string, FactorValue>): { label: string; value: number }[] {
  const coherence = Math.round(
    (numOf(values, 'word_precision') + (100 - numOf(values, 'sentence_complexity')) * 0.5) / 1.5,
  );
  const completeness = Math.round(
    (numOf(values, 'clarity') + numOf(values, 'context_completeness') + numOf(values, 'task_clarity')) / 3,
  );
  return [
    { label: '相关性', value: numOf(values, 'relevance') },
    { label: '创造性', value: numOf(values, 'creativity') },
    { label: '事实准确', value: numOf(values, 'accuracy') },
    { label: '连贯性', value: Math.max(0, Math.min(100, coherence)) },
    { label: '完整性', value: Math.max(0, Math.min(100, completeness)) },
  ];
}

/** 因素 → 行文本（导出 CSV 用） */
export function factorRow(def: PromptFactorDef, value: FactorValue): Record<string, string> {
  const display =
    def.control === 'toggle' ? (value === true ? '开启' : '关闭') :
    def.control === 'select' ? (def.options?.find((o) => o.value === value)?.label ?? String(value)) :
    typeof value === 'number' && def.unit ? `${value}${def.unit}` : String(value);
  return {
    分类: CATEGORIES.find((c) => c.id === def.category)?.label ?? def.category,
    因素: def.name,
    当前值: display,
    默认值: typeof def.defaultValue === 'boolean' ? (def.defaultValue ? '开启' : '关闭') : String(def.defaultValue),
    类型: def.custom ? '自定义' : '内置',
    说明: def.desc,
  };
}

/**
 * 导出为 JSON 字符串（不包含 apiKey）。
 * systemText = 后端「身份提示词」生成的注入模板（见 identityPrompt.ts），
 * 为空时表示线上配置未加载/不可用，导出仍以因素数据为主。
 */
export function exportJson(
  values: Record<string, FactorValue>,
  customFactors: PromptFactorDef[],
  systemText: string,
  contextWindow: number,
): string {
  const defs = allFactors(customFactors);
  const report = usageReport(systemText, buildPrompt(values), contextWindow);
  return JSON.stringify(
    {
      app: '云枢 · 提示词影响因素实验室',
      version: 3,
      exportedAt: new Date().toISOString(),
      prompt: buildPrompt(values),
      systemPrompt: systemText,
      tokenEstimate: {
        systemTokens: report.systemTotal,
        userPromptTokens: report.userTokens,
        totalTokens: report.total,
        contextWindow: report.windowSize,
        usedPct: report.usedPct,
      },
      factors: defs.map((d) => ({
        id: d.id,
        category: d.category,
        name: d.name,
        control: d.control,
        value: values[d.id] ?? d.defaultValue,
      })),
    },
    null,
    2,
  );
}

/** 导出为 CSV 字符串 */
export function exportCsv(values: Record<string, FactorValue>, customFactors: PromptFactorDef[]): string {
  const defs = allFactors(customFactors);
  const header = ['分类', '因素', '当前值', '默认值', '类型', '说明'];
  const rows = defs.map((d) => factorRow(d, values[d.id] ?? d.defaultValue));
  const escape = (s: string) => `"${s.replace(/"/g, '""')}"`;
  return [header.map(escape).join(','), ...rows.map((r) => header.map((h) => escape(r[h] ?? '')).join(','))].join('\n');
}

/**
 * 走**云枢后端代理**的真实预览（默认路径，2026-10-14 修 404）。
 *
 * 【为什么默认走它】前端直连用户填的 Endpoint 有三个坑：面板默认值是空串、外部域名
 * 被浏览器 CORS 挡（响应体为空 ⇒ 界面只显示 "HTTP 404："）、路径写错同样是 404。
 * 后端 /api/prompt-lab/llm-preview 用 .env 里已配置的 LLM 生成，开箱可用。
 */
export async function requestBackendLlmPreview(opts: {
  systemPrompt: string;
  prompt: string;
  model?: string;
  maxTokens: number;
}): Promise<{ ok: true; text: string } | { ok: false; error: string }> {
  try {
    const body = await postEnvelope<{ text?: string }>(PROMPT_LAB_LLM_PREVIEW, {
      systemPrompt: opts.systemPrompt,
      prompt: opts.prompt,
      model: opts.model || '',
      maxTokens: opts.maxTokens,
    });
    return { ok: true, text: String(body?.text ?? '') };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * 真实 LLM 预览接口（**自定义Endpoint 直连**）：兼容 OpenAI 风格 chat/completions。
 * 仅在用户显式取消「使用云枢默认 LLM」时使用；直连受浏览器 CORS 约束。
 */
export async function requestLlmPreview(opts: {
  endpoint: string;
  apiKey: string;
  model: string;
  systemPrompt: string;
  prompt: string;
  temperature: number;
  topP: number;
  maxTokens: number;
}): Promise<{ ok: true; text: string } | { ok: false; error: string }> {
  try {
    const res = await fetch(opts.endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(opts.apiKey ? { Authorization: `Bearer ${opts.apiKey}` } : {}),
      },
      body: JSON.stringify({
        model: opts.model,
        messages: [
          { role: 'system', content: opts.systemPrompt },
          { role: 'user', content: opts.prompt },
        ],
        temperature: opts.temperature,
        top_p: opts.topP,
        max_tokens: opts.maxTokens,
      }),
    });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}：${(await res.text()).slice(0, 300)}` };
    const data = await res.json();
    const text = data?.choices?.[0]?.message?.content ?? data?.output?.text ?? JSON.stringify(data);
    return { ok: true, text: String(text) };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}
