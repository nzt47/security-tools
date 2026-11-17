/**
 * PreviewPanel —— 提示词实验室右侧实时预览面板
 * 从 prompt-lab/index.tsx 拆分（原 aside 部分）。
 *
 * 【2026-11-15 重排：去掉「模拟 / 真实接口」双模式，再去掉"模拟输出"】
 * Owner 原话：「实时预览怎么还分 模拟 和 真实接口？看不懂。」→「你真知我，按你说的做。」
 * 原来的设计把两件不同的事并列成"两种预览模式"，而"模拟"其实**不是模型输出** ——
 * 它只是把滑块组合翻译成人话（lib/promptFactors.simulateOutput），却长得像一段回答。
 * 现在（两步走完）：
 *   1. 主路径只有一条 —— 「试跑一次（真实模型）」，一个按钮，默认走云枢已配置的模型；
 *   2. 自己填 Endpoint/API Key 的入口收进折叠的「高级」里（默认不用碰）；
 *   3. **"模拟输出"整块删除**（连带 lib 里的 simulateOutput 一起删）：面板只留
 *      系统提示词 / 生成的提示词 / 真跑结果 / 效果评估 / Token / 导出。
 * 组件不再接收 mode / onModeChange / sim。
 */
import { Loader2, Settings2, Download, Wrench } from 'lucide-react';
import { Button, Input } from '@/components/ui'
import { usePromptLabStore } from '../../stores/usePromptLabStore';
import RadarChart from './RadarChart';

export interface PreviewUsage {
  systemTotal: number;
  userTokens: number;
  total: number;
  windowSize: number;
  usedPct: number;
}

interface PreviewPanelProps {
  /**
   * 这个预览属于哪个 Tab（2026-11-15 Owner：「把 身份提示词 及身份提示词变化产生的实时预览
   * 拆成一个 TAB」）：
   *  · 'identity' —— 只显示「最终注入文本」（由 ① 分节配置的开关组装）
   *  · 'factors'  —— 显示因素滑块的产物：生成的提示词 / 试跑 / 评估 / Token / 导出
   */
  part: 'identity' | 'factors';
  /**
   * 「不进这段模板」的启用节（emit_stage ≠ system_prompt 的那几条：tools 参数 / 用户消息前置 /
   * 运行期注入）。2026-11-15 Owner 问「为什么到 ## 记忆线索 {memory_context} 就没有下文了」——
   * 就是因为 LifeTrace / Persona / 工作记忆 / 工具定义这些**本来就不在模板里**，它们按发出阶段
   * 走别的通道；这段文本只到模板的最后一行。列表由界面按后端 emit_stage 现算，不写死。
   */
  offTemplateSections?: { key: string; label: string; stageLabel: string; stage: string }[];
  /** 注入 system message：后端身份提示词配置生成的模板（可含运行时占位符） */
  systemPrompt: string;
  /** 聚焦面板项的发出内容：命中则在该段高亮（启用状态 ↔ 内容显示联动） */
  highlightSnippet?: string | null;
  prompt: string;
  radar: { label: string; value: number }[];
  token: PreviewUsage;
  llmOutput: string | null;
  llmError: string | null;
  llmLoading: boolean;
  onRunLlm: () => void;
  onExport: (kind: 'json' | 'csv') => void;
}

/** 把模板按高亮片段切成 [前, 片段, 后]，未命中时返回 null（不做高亮） */
export function splitHighlight(
  text: string,
  snippet?: string | null,
): [string, string, string] | null {
  const s = (snippet ?? '').trim();
  if (!text || !s) return null;
  const idx = text.indexOf(s);
  if (idx < 0) return null;
  return [text.slice(0, idx), s, text.slice(idx + s.length)];
}

export default function PreviewPanel({
  part,
  offTemplateSections,
  systemPrompt,
  highlightSnippet,
  prompt,
  radar,
  token,
  llmOutput,
  llmError,
  llmLoading,
  onRunLlm,
  onExport,
}: PreviewPanelProps) {
  const llm = usePromptLabStore((s) => s.llm);
  const setLlm = usePromptLabStore((s) => s.setLlm);
  const hl = splitHighlight(systemPrompt, highlightSnippet);

  if (part === 'identity') {
    // 身份提示词 Tab：这里只有「身份提示词变化产生的实时预览」这一块
    // （Owner 2026-11-15：「把 身份提示词 及身份提示词变化产生的实时预览拆成一个 TAB」）。
    return (
      <aside className="pl-preview">
        <div className="pl-preview-head">
          <h2>实时预览</h2>
        </div>
        <div className="pl-preview-block">
          <h3>最终注入文本（只读）</h3>
          {/* 这块只由左侧「分节配置」的开关组装；因素滑块改的是另一个 Tab 的「生成的提示词」。
              2026-11-15 补两句「哪份是真发的 / 为什么到记忆线索就没了」，见下面与 offTemplateSections。 */}
          <p className="pl-export-hint">
            这一段就是<b>线上每次对话真正发出的 system message</b>（配置模板 → 应用后写
            <code> data/system_prompt.txt</code>，真实请求按同一份组装）。预览里
            <code>{'{…}'}</code> 占位符<b>不代入</b>——真实请求才填值。
          </p>
          <pre className="pl-sim-out">
            {systemPrompt ? (
              hl ? (
                <>
                  {hl[0]}
                  <mark className="pl-hl" title="左侧「身份提示词」当前聚焦的配置项发出的内容">
                    {hl[1]}
                  </mark>
                  {hl[2]}
                </>
              ) : (
                systemPrompt
              )
            ) : (
              '（还没读到注入模板 —— 左侧「分节配置」载入后，这里会随每个开关实时更新）'
            )}
          </pre>

          {/* 「模板到此为止」——Owner 2026-11-15 的原话是"为什么到 ## 记忆线索 {memory_context}
              就没有下文了？LifeTrace 语义检索 以下都没有了"。答案两半：
              ① 模板里只有 system_prompt 阶段的节，剩下的占位符本来就到这一行为止；
              ② LifeTrace / Persona / 工作记忆 / 工具定义 走的是别的通道，不在这一段文本里。
              列表按后端 emit_stage 现算，B 级不写死名字（新增运行期节自动出现）。 */}
          <div className="pl-off-template">
            <div className="pl-off-template-head">模板到此为止 —— 下面这些启用节<b>不在</b>这段文本里</div>
            <ul>
              {(offTemplateSections ?? []).map((s) => (
                <li key={s.key}>
                  <span className="pl-off-template-name">{s.label}</span>
                  <span className="pl-off-template-stage">{s.stageLabel || s.stage}</span>
                </li>
              ))}
              {(offTemplateSections ?? []).length === 0 && <li className="pl-off-template-none">（没有：所有启用节都在模板里）</li>}
            </ul>
            <p>
              「记忆线索」那一行在 system message 里<b>恒为空</b>：模板只承载稳定前缀，检索到的
              工作记忆 / 长期记忆片段按「稳定前缀 + 易变尾部」随<b>本轮最后一条消息</b>下发
              （轻量档不检索 ⇒ 那一段就空着；检索 / 完整·流式档实测会带上，见「LLM 通信监控」里
              那条请求的最后一条 user 消息）。要看实际注入内容，去那里看原文。
            </p>
            <p>
              标「运行期注入」的是<b>并入型</b>：LifeTrace 的检索结果并进上面「记忆线索」那一段、
              Persona 的人格文本接在记忆线索之后——所以它们不会在模板里另起一段，而是让
              <code>{'{memory_context}'}</code> 那一行在真实请求里变成有内容的几行。
              标「tools 参数」的走请求体的 <code>tools</code> 字段，标「用户消息前置」的拼在你的
              那句话前面。真实请求里这些位置到底填了什么，去「LLM 通信监控」看 system_prompt 原文。
            </p>
          </div>
        </div>
      </aside>
    )
  }

  // 因素 Tab：滑块能改的东西（生成的提示词 / 试跑 / 评估 / Token / 导出）
  return (
    <aside className="pl-preview">
      <div className="pl-preview-head">
        <h2>实时预览</h2>
      </div>

      <div className="pl-preview-block">
        <h3>生成的提示词（左侧因素实时生成 · 试跑用它）</h3>
        {/* 两份预览里"哪份是真发的"必须写死在这里：这一份**不是**线上对话的 system message，
            它只在点「跑一次看效果」时作为 user 消息发出去。 */}
        <p className="pl-export-hint">
          这一份是<b>试跑时</b>作为 user 消息发出去的文本；线上真实对话发的是你自己输入的那句话，
          <b>不会</b>用这一份。真发的 system message 在「身份提示词」Tab 里。
        </p>
        <textarea className="pl-prompt-out" readOnly value={prompt} rows={9} spellCheck={false} />
      </div>

      {/* 唯一的"输出"入口：真跑一次。默认走云枢已配置的模型，不需要填任何 Key。 */}
      <div className="pl-preview-block">
        <h3>试跑一次（真实模型）</h3>
        <p className="pl-export-hint">
          用当前这套提示词真跑一次，看模型实际怎么答。默认走云枢 <code>.env</code> 里已配置的模型
          —— 不用填 Key，每点一次消耗一次 token；改过滑块/参数后旧的回答会清空，需要重新点。
        </p>
        <div className="pl-preview-actions">
          <Button type="button" variant="primary" size="sm" onClick={onRunLlm} disabled={llmLoading}>
            {llmLoading ? <Loader2 size={13} className="spin" /> : <Wrench size={13} />}
            {llmLoading ? '请求中…' : '跑一次看效果'}
          </Button>
        </div>
        {llmError && <p className="pl-error">{llmError}</p>}
        <pre className="pl-sim-out">
          {llmOutput ?? '还没跑过。点上面的按钮，用当前提示词请求一次模型。'}
        </pre>

        {/* 高级：只有想绕开云枢默认模型、自己直连 OpenAI 兼容地址时才需要展开 */}
        <details className="pl-llm-config">
          <summary>
            <Settings2 size={12} /> 高级：改用自己填的接口（默认不用管）
          </summary>
          <label className="pl-field">
            <span>用云枢默认 LLM</span>
            <input
              type="checkbox"
              checked={llm.useBackend}
              onChange={(e) => setLlm({ useBackend: e.target.checked })}
            />
          </label>
          <p className="pl-export-hint">
            勾选（默认）＝由后端用 <code>.env</code> 里已配置的模型生成，无需 Endpoint / API Key。
            取消勾选才直连你自己填的 OpenAI 兼容地址（直连受浏览器 CORS 限制，容易只拿到
            <code>HTTP 4xx</code>）。
          </p>
          <label className="pl-field">
            <span>Endpoint</span>
            <Input value={llm.endpoint} disabled={llm.useBackend} onChange={(e) => setLlm({ endpoint: e.target.value })} placeholder="https://api.openai.com/v1/chat/completions" size="sm" />
          </label>
          <label className="pl-field">
            <span>API Key</span>
            <Input type="password" value={llm.apiKey} disabled={llm.useBackend} onChange={(e) => setLlm({ apiKey: e.target.value })} placeholder="sk-…（仅本地存储）" size="sm" />
          </label>
          <label className="pl-field">
            <span>模型</span>
            <Input value={llm.model} onChange={(e) => setLlm({ model: e.target.value })} size="sm" />
          </label>
        </details>
      </div>

      <div className="pl-preview-block">
        <h3>效果评估</h3>
        <RadarChart data={radar} />
      </div>

      <div className="pl-preview-block">
        {/* 【2026-11-15】这里显示的是**各节声明的 token 预算**之和，不是一次请求的实测用量
            （实测：一次真实请求 8.8k，其中 tools 定义 7.6k、system 提示词只有 ~130 tok、
            记忆线索实测只有几十字符，而面板曾把记忆线索按 5000 计）。改标题 + 指路，
            免得按这些数字去砍不存在的开销。 */}
        <h3>Token 预算（各节声明值 · 非实测）</h3>
        <p className="pl-export-hint">
          真实用量请看「LLM 通信监控」里每次请求的 usage —— 那边的 tools 段通常是大头
          （本部署实测：26 个工具 ≈ 7.6k tok、system 提示词 ≈ 130 tok、缓存命中率 ~97%）。
        </p>
        <div className="pl-token-table">
          <div className="pl-token-row">
            <span>身份提示词模板（注入）</span>
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
            <Input type="number" value={llm.contextWindow} min={1024} step={1024} onChange={(e) => setLlm({ contextWindow: Math.max(1024, Number(e.target.value) || 32768) })} size="sm" />
          </label>
        </div>
      </div>

      <div className="pl-export">
        <h3>数据导出</h3>
        <p className="pl-export-hint">导出当前参数组合与身份提示词模板，便于提示词优化分析（JSON 可再导入，CSV 用于表格处理）。</p>
        <div className="pl-export-actions">
          <Button type="button" variant="default" size="sm" onClick={() => onExport('json')}>
            <Download size={13} /> 导出 JSON
          </Button>
          <Button type="button" variant="default" size="sm" onClick={() => onExport('csv')}>
            <Download size={13} /> 导出 CSV
          </Button>
        </div>
      </div>
    </aside>
  );
}
