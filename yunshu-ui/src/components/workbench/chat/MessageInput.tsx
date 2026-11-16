/**
 * 消息输入框：Enter 发送 / Shift+Enter 换行 / 自动增高
 */
import { useEffect, useRef } from 'react';
import { CornerDownLeft } from 'lucide-react';
import { Button } from '@/components/ui';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled?: boolean;
}

export function MessageInput({ value, onChange, onSend, disabled }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
    <div className="wb-input-wrap">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
        placeholder="输入消息，Enter 发送，Shift+Enter 换行…"
        className="wb-input"
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
  );
}
