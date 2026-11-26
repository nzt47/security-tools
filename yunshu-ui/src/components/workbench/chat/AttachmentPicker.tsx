/**
 * 附件选择器（E3-3）：📎 选文件 → POST /api/chat/attachments → 显示 chips。
 *
 * 【为什么上传完才拼 manifest】后端只负责「稳定落盘 + 回可读路径」；消息里那行
 * 「附件：name → path」由 ChatPanel 在发送时拼进正文（用户看得见、可复核，模型按需 read_file）。
 *
 * 【失败姿态】上传失败不阻塞发送：只给一行提示并保持原有附件列表（不悄悄丢掉已选文件）。
 */
import { useRef, useState } from 'react';
import { Paperclip, X } from 'lucide-react';
import { Button, Input } from '@/components/ui';
import { CHAT_ATTACHMENTS } from '@/api/endpoints';
import { authHeader } from '../../../lib/apiToken';

export interface AttachmentMeta {
  path: string;
  name: string;
  bytes: number;
  kind: 'image' | 'text' | 'binary' | string;
  ocr?: { available: boolean; text?: string; note?: string };
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

/** 把已上传附件拼成消息前缀（附件清单：文件名 → 路径，图片附 OCR 摘要） */
export function buildAttachmentManifest(list: AttachmentMeta[]): string {
  if (!list.length) return '';
  const lines = ['【附件】'];
  for (const a of list) {
    const ocr =
      a.ocr && a.ocr.available && a.ocr.text
        ? '；OCR：' + a.ocr.text.replace(/\s+/g, ' ').slice(0, 500)
        : '';
    lines.push('- ' + a.name + '（' + formatBytes(a.bytes) + '）：' + a.path + ocr);
  }
  return lines.join('\n') + '\n\n';
}

export function AttachmentPicker({
  sessionId,
  value,
  onChange,
  disabled,
}: {
  sessionId?: string;
  value: AttachmentMeta[];
  onChange: (next: AttachmentMeta[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function upload(file: File) {
    setBusy(true);
    setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      if (sessionId) form.append('session_id', sessionId);
      const res = await fetch(CHAT_ATTACHMENTS, {
        method: 'POST',
        headers: authHeader(),
        body: form,
      });
      const body = (await res.json().catch(() => null)) as
        | { code?: number; data?: AttachmentMeta }
        | null;
      if (!res.ok || !body || body.code !== 200 || !body.data) throw new Error('upload failed');
      onChange([...value, body.data]);
    } catch {
      setError('上传失败');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  }

  return (
    <div className="wb-attach">
      {/* 用 Input 原语（不是裸 input 标签）：本仓 UI 守卫按参数化原语计分，
          裸 input 会把 raw_input 指标顶上去（同一形态在 replicate 导入处也是这么写的）。 */}
      <Input
        ref={inputRef}
        variant="bare"
        type="file"
        className="wb-attach-input"
        inputClassName="text-caption"
        aria-label="选择附件"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void upload(f);
        }}
        disabled={disabled || busy}
      />
      <Button
        type="button"
        variant="plain"
        size="bare"
        className="wb-vote"
        aria-label="上传附件"
        title={busy ? '上传中…' : '上传附件（文档/图片）'}
        disabled={disabled || busy}
        onClick={() => inputRef.current?.click()}
      >
        <Paperclip size={13} />
      </Button>
      {value.map((a) => (
        <span key={a.path} className="wb-attach-chip" title={a.path}>
          <span className="max-w-40 truncate">{a.name}</span>
          <span className="text-micro text-slate-500">{formatBytes(a.bytes)}</span>
          <Button
            type="button"
            variant="plain"
            size="bare"
            aria-label={'移除附件 ' + a.name}
            title="移除"
            onClick={() => onChange(value.filter((x) => x.path !== a.path))}
          >
            <X size={11} />
          </Button>
        </span>
      ))}
      {error ? <span className="text-micro text-rose-400">{error}</span> : null}
    </div>
  );
}
