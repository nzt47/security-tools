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

  return (
    <aside className="pl-preview">
      <div className="pl-preview-head">
        <h2>实时预览</h2>
      </div>

      <div className="pl-preview-block">
        <h3>系统提示词（身份提示词 · 注入）</h3>
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
            '（线上身份提示词模板未加载——编辑/保存左侧「身份提示词」区后此处实时更新）'
          )}
        </pre>
      </div>

      <div className="pl-preview-block">
        <h3>生成的提示词</h3>
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
          <button type="button" className="pl-btn primary" onClick={onRunLlm} disabled={llmLoading}>
            {llmLoading ? <Loader2 size={13} className="spin" /> : <Wrench size={13} />}
            {llmLoading ? '请求中…' : '跑一次看效果'}
          </button>
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
            <input className="pl-text" value={llm.endpoint} disabled={llm.useBackend} onChange={(e) => setLlm({ endpoint: e.target.value })} placeholder="https://api.openai.com/v1/chat/completions" />
          </label>
          <label className="pl-field">
            <span>API Key</span>
            <input className="pl-text" type="password" value={llm.apiKey} disabled={llm.useBackend} onChange={(e) => setLlm({ apiKey: e.target.value })} placeholder="sk-…（仅本地存储）" />
          </label>
          <label className="pl-field">
            <span>模型</span>
            <input className="pl-text" value={llm.model} onChange={(e) => setLlm({ model: e.target.value })} />
          </label>
        </details>
      </div>

      <div className="pl-preview-block">
        <h3>效果评估</h3>
        <RadarChart data={radar} />
      </div>

      <div className="pl-preview-block">
        <h3>Token 用量估算</h3>
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
        <p className="pl-export-hint">导出当前参数组合与身份提示词模板，便于提示词优化分析（JSON 可再导入，CSV 用于表格处理）。</p>
        <div className="pl-export-actions">
          <button type="button" className="pl-btn" onClick={() => onExport('json')}>
            <Download size={13} /> 导出 JSON
          </button>
          <button type="button" className="pl-btn" onClick={() => onExport('csv')}>
            <Download size={13} /> 导出 CSV
          </button>
        </div>
      </div>
    </aside>
  );
}
