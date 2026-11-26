/**
 * 消息输入框：Enter 发送 / Shift+Enter 换行 / 自动增高
 *
 * 【E3-3 · 2026-11-27】新增引用条：点某条消息的「引用」后，这里显示一行摘要；
 * 发送时由 ChatPanel 把原文拼成 Markdown 引用块前缀（本组件不碰消息内容）。
 */
import { useEffect, useRef, useState } from 'react';
import { CornerDownLeft, X } from 'lucide-react';
import { Button, Textarea } from '@/components/ui';
import { FileMentionMenu, applyMention, extractMentionQuery } from './FileMentionMenu';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled?: boolean;
  /** 待引用的消息原文（空 = 无引用） */
  quoted?: string;
  onClearQuote?: () => void;
}

export function MessageInput({ value, onChange, onSend, disabled, quoted, onClearQuote }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 【E3-3】@ 文件引用：只认「末尾正在输入的 @片段」；关掉后同一片段不再弹（改字再弹）
  const mentionQuery = extractMentionQuery(value);
  const [mentionClosedFor, setMentionClosedFor] = useState<string | null>(null);
  const showMention = mentionQuery !== null && mentionQuery.trim().length >= 2
    && mentionClosedFor !== mentionQuery;

  // 输入后自动增高（封顶约 15 行 ≈ 320px；超高才出现细滚动条）
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  };

  return (
    <div className="wb-input-col">
      {/* 引用条放在 .wb-input-wrap **外面**：那一层是 flex row（textarea + 发送键），
          塞进去会变成第三个行内元素，把输入框挤窄。 */}
      {quoted ? (
        <div className="wb-quote-bar">
          <span className="min-w-0 flex-1 truncate text-caption text-slate-400">
            引用：{quoted.replace(/\s+/g, ' ').slice(0, 80)}
          </span>
          <Button
            type="button"
            variant="plain"
            size="bare"
            aria-label="取消引用"
            title="取消引用"
            onClick={onClearQuote}
          >
            <X size={12} />
          </Button>
        </div>
      ) : null}
      {showMention && mentionQuery !== null ? (
        <FileMentionMenu
          query={mentionQuery}
          onPick={(path) => {
            onChange(applyMention(value, path));
            setMentionClosedFor(null);
            textareaRef.current?.focus();
          }}
          onClose={() => setMentionClosedFor(mentionQuery)}
        />
      ) : null}
      <div className="wb-input-wrap">
        {/* 【2026-11-19】外观/自动长高仍由 .wb-input + 本组件的 useEffect 决定；原语只提供 ref 转发与一致性 */}
        <Textarea
          variant="bare"
          className="wb-input"
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={1}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行…"
        />
        <Button
          type="button"
          variant="primary"
          size="icon"
          onClick={onSend}
          disabled={disabled || !value.trim()}
          aria-label="发送"
          title="发送"
          className="mb-1.5 shrink-0 self-end"
        >
          <CornerDownLeft size={15} />
        </Button>
      </div>
    </div>
  );
}
