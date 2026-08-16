/**
 * 提示词影响因素 · 数据模型 / 默认因素 / 模拟预览引擎
 * ------------------------------------------------
 * 设计约束（不易）：
 *  - 因素定义（def）与"当前值"分离：def 描述"有什么因素、怎么调节"，
 *    当前值由 usePromptLabStore 持久化。默认因素不可改，仅可调值。
 *  - 因素 id 是持久化锚点（LocalStorage 键值），改名/删号即丢配置。
 *  - 所有纯数据，无 React 依赖，便于单测与导出。
 */
import type { FactorCategory, PromptFactorDef, FactorValue } from './promptFactorTypes';

export type { FactorCategory, PromptFactorDef, FactorValue };

/** 五大分类元数据：id 为锚点，label 用于界面展示 */
export const CATEGORIES: { id: FactorCategory; label: string; short: string; desc: string; color: string }[] = [
  { id: 'structure', label: '提示词结构要素', short: '结构', desc: '提示词的骨架与组织方式', color: '#22d3ee' },
  { id: 'language', label: '语言表达特征', short: '语言', desc: '措辞、句式与术语的风格', color: '#a78bfa' },
  { id: 'context', label: '上下文参数控制', short: '上下文', desc: '对话历史、角色与背景注入', color: '#fbbf24' },
  { id: 'model', label: '模型参数调节', short: '模型', desc: '采样与输出长度等生成控制', color: '#34d399' },
  { id: 'evaluation', label: '效果评估指标', short: '评估', desc: '对输出质量的多维量化评估', color: '#f87171' },
];

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
];

/** 默认 id → 默认值映射（重置用） */
export const DEFAULT_VALUES: Record<string, FactorValue> = Object.fromEntries(
  DEFAULT_FACTORS.map((f) => [f.id, f.defaultValue]),
);

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

/**
 * 模拟预览引擎：根据当前因素值拼装一段"模拟输出"。
 * 无真实 LLM 依赖，仅做直观演示；接入真实接口后可切换（见 requestLlmPreview）。
 */
export function simulateOutput(values: Record<string, FactorValue>): string {
  const temp = numOf(values, 'temperature');
  const topP = numOf(values, 'top_p');
  const maxTok = numOf(values, 'max_tokens');
  const clarity = numOf(values, 'clarity');
  const taskClarity = numOf(values, 'task_clarity');
  const precision = numOf(values, 'word_precision');
  const complexity = numOf(values, 'sentence_complexity');
  const term = strOf(values, 'terminology');
  const history = numOf(values, 'history_len');
  const role = strOf(values, 'role_setting');

  const style =
    temp >= 1.2 ? '发散性强、联想丰富' :
    temp >= 0.9 ? '灵活、带适度创意' :
    temp >= 0.5 ? '平衡、稳定可靠' : '严谨、保守、低风险';
  const focus = topP >= 0.9 ? '词汇分布广、多样性高' : topP >= 0.7 ? '适度聚焦' : '高度聚焦、同质化倾向';
  const structure = clarity + taskClarity >= 140 ? '结构清晰、分点到位' : '结构一般、偶有偏离';
  const wording =
    term === 'heavy' ? '大量专业术语，面向内行读者' :
    term === 'none' ? '通俗措辞，面向大众读者' : '专业术语与通俗表达兼顾';
  const precisionDesc = precision >= 70 ? '措辞精确、歧义少' : '措辞偶有含糊';
  const sentence = complexity >= 70 ? '以长句和复杂从句为主' : complexity >= 40 ? '长短句适中' : '以短句为主、直白易读';
  const ctx = history > 0 ? `可参考最近 ${history} 轮对话的线索` : '仅凭单轮问题作答，可能缺乏前情';

  return (
    `【${role || '未设定角色'}】\n` +
    `基于当前参数组合（temperature=${temp.toFixed(1)} / top_p=${topP.toFixed(2)} / max_tokens=${maxTok}），` +
    `模拟输出风格为：${style}，${focus}；文本组织${structure}；${wording}，${precisionDesc}，${sentence}；${ctx}。\n` +
    `（说明：此片段为参数驱动的模拟预览，用于观察各因素组合对输出的影响趋势，非真实模型输出。）`
  );
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

/** 导出为 JSON 字符串（不包含 apiKey） */
export function exportJson(values: Record<string, FactorValue>, customFactors: PromptFactorDef[]): string {
  const defs = allFactors(customFactors);
  return JSON.stringify(
    {
      app: '云枢 · 提示词影响因素实验室',
      version: 1,
      exportedAt: new Date().toISOString(),
      prompt: buildPrompt(values),
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
 * 真实 LLM 预览接口（预留）：兼容 OpenAI 风格 chat/completions。
 * 未配置 endpoint/apiKey 时由页面降级到模拟预览。
 */
export async function requestLlmPreview(opts: {
  endpoint: string;
  apiKey: string;
  model: string;
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
        messages: [{ role: 'user', content: opts.prompt }],
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
