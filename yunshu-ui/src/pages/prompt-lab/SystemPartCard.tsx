/**
 * SystemPartCard —— 系统提示词组件卡片（启停 + 文本编辑 + token 估算）
 * 从 PromptLab.tsx 拆分（原 199-242 行，逻辑不变）。内置不可删，自定义可删。
 */
import { Trash2 } from 'lucide-react';
import { estimateTokens } from '../../lib/promptFactors';
import type { SystemPart } from '../../lib/promptFactorTypes';

interface SystemPartCardProps {
  part: SystemPart;
  onUpdate: (id: string, patch: Partial<SystemPart>) => void;
  onRemove?: (id: string) => void;
}

export default function SystemPartCard({ part, onUpdate, onRemove }: SystemPartCardProps) {
  return (
    <div className="pl-factor-card pl-syspart">
      <div className="pl-factor-head">
        <span className="pl-factor-name">{part.label}</span>
        <div className="flex items-center gap-2">
          <span className="pl-token-chip" title="该组件估算 token 数">
            ~{estimateTokens(part.text)} tok
          </span>
          <button
            type="button"
            className={`pl-toggle ${part.enabled ? 'on' : ''}`}
            onClick={() => onUpdate(part.id, { enabled: !part.enabled })}
            aria-pressed={part.enabled}
          >
            <span className="pl-toggle-dot" />
            {part.enabled ? '注入' : '禁用'}
          </button>
          {!part.builtin && (
            <button type="button" className="pl-icon-btn" onClick={() => onRemove?.(part.id)} title="删除此自定义组件">
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
      <textarea
        className={`pl-textarea pl-syspart-text ${part.enabled ? '' : 'disabled'}`}
        rows={2}
        value={part.text}
        disabled={!part.enabled}
        onChange={(e) => onUpdate(part.id, { text: e.target.value })}
        spellCheck={false}
      />
    </div>
  );
}
