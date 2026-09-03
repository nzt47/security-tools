/**
 * PreviewPanel —— 提示词实验室右侧实时预览面板
 * 从 prompt-lab/index.tsx 拆分（原 aside 部分，逻辑不变）。
 * 内部经 store 获取 llm 配置，其余数据经 props 传入。
 *
 * 深度合并说明：systemPrompt = 后端「身份提示词」生成的注入模板
 * （见 identityPrompt.ts），本地不再维护 7 段沙箱组件，token 块相应
 * 简化为「模板文本 + 用户提示词」的总量估算。
 */
import { Loader2, Settings2, Download, Wrench } from 'lucide-react';
import { usePromptLabStore } from '../../stores/usePromptLabStore';
import RadarChart from './RadarChart';

export type PreviewMode = 'sim' | 'llm';

export interface PreviewUsage {
  systemTotal: number;
  userTokens: number;
  total: number;
  windowSize: number;
  usedPct: number;
}

interface PreviewPanelProps {
  mode: PreviewMode;
  onModeChange: (m: PreviewMode) => void;
  /** 注入 system message：后端身份提示词配置生成的模板（可含运行时占位符） */
  systemPrompt: string;
  prompt: string;
  sim: string;
  radar: { label: string; value: number }[];
  token: PreviewUsage;
  llmOutput: string | null;
  llmError: string | null;
  llmLoading: boolean;
  onRunLlm: () => void;
  onExport: (kind: 'json' | 'csv') => void;
}

export default function PreviewPanel({
  mode,
  onModeChange,
  systemPrompt,
  prompt,
  sim,
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

  return (
    <aside className="pl-preview">
      <div className="pl-preview-head">
        <h2>实时预览</h2>
        <div className="pl-mode-switch" role="tablist" aria-label="预览模式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'sim'}
            className={mode === 'sim' ? 'active' : ''}
            onClick={() => onModeChange('sim')}
          >
            模拟
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'llm'}
            className={mode === 'llm' ? 'active' : ''}
            onClick={() => onModeChange('llm')}
          >
            真实接口
          </button>
        </div>
      </div>

      {mode === 'llm' && (
        <details className="pl-llm-config" open>
          <summary>
            <Settings2 size={12} /> LLM 接口配置（OpenAI 兼容）
          </summary>
          <label className="pl-field">
            <span>Endpoint</span>
            <input className="pl-text" value={llm.endpoint} onChange={(e) => setLlm({ endpoint: e.target.value })} placeholder="https://api.openai.com/v1/chat/completions" />
          </label>
          <label className="pl-field">
            <span>API Key</span>
            <input className="pl-text" type="password" value={llm.apiKey} onChange={(e) => setLlm({ apiKey: e.target.value })} placeholder="sk-…（仅本地存储）" />
          </label>
          <label className="pl-field">
            <span>模型</span>
            <input className="pl-text" value={llm.model} onChange={(e) => setLlm({ model: e.target.value })} />
          </label>
        </details>
      )}

      <div className="pl-preview-block">
        <h3>系统提示词（身份提示词 · 注入）</h3>
        <pre className="pl-sim-out">
          {systemPrompt
            ? systemPrompt
            : '（线上身份提示词模板未加载——编辑/保存左侧「身份提示词」区后此处实时更新）'}
        </pre>
      </div>

      <div className="pl-preview-block">
        <h3>生成的提示词</h3>
        <textarea className="pl-prompt-out" readOnly value={prompt} rows={9} spellCheck={false} />
      </div>

      <div className="pl-preview-block">
        <h3>{mode === 'sim' ? '模拟输出' : '真实输出'}</h3>
        {mode === 'llm' ? (
          <>
            <div className="pl-preview-actions">
              <button type="button" className="pl-btn primary" onClick={onRunLlm} disabled={llmLoading}>
                {llmLoading ? <Loader2 size={13} className="spin" /> : <Wrench size={13} />}
                {llmLoading ? '请求中…' : '请求真实输出'}
              </button>
            </div>
            {llmError && <p className="pl-error">{llmError}</p>}
            <pre className="pl-sim-out">{llmOutput ?? '尚未请求。参数变化后请重新点击"请求真实输出"。'}</pre>
          </>
        ) : (
          <pre className="pl-sim-out">{sim}</pre>
        )}
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
