/**
 * PresentCard —— 交付文件卡（E1-3a）
 * ------------------------------------------------
 * 位置：消息区末尾（最后一条回复下方）。数据：store.presented（SSE `present` 事件）。
 * 只显示路径/标题 + 复制；**不读取文件内容**（服务端也只做字符串校验）。
 * 工作区内的文件可"在文件管理器中显示"（调既有 workspace reveal，仅目录）。
 */
import { useState } from 'react';
import { Check, Copy, FileText, FolderOpen } from 'lucide-react';
import { Badge, Button, Card } from '@/components/ui';
import { sessionApi } from '@/lib/sessionApi';
import type { PresentedFile } from '@/lib/sse';
import { useLayoutStore } from '@/stores/useLayoutStore';

export function PresentCard({ files, note }: { files: PresentedFile[]; note?: string }) {
  const sessionId = useLayoutStore((s) => s.activeSessionId);
  const [copied, setCopied] = useState<string | null>(null);
  const [revealError, setRevealError] = useState<string | null>(null);

  const copy = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
      setCopied(path);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* 剪贴板不可用时静默（与 Markdown 代码块同口径） */
    }
  };

  const revealWorkspace = async () => {
    if (!sessionId) return;
    setRevealError(null);
    try {
      // 复用既有「在文件管理器中打开会话工作区」端点（只对工作区目录，不读任意文件）
      await sessionApi.revealWorkspace(sessionId);
    } catch (e) {
      setRevealError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card data-testid="present-card" className="mt-1 border-slate-700/60 bg-slate-900/40 p-2.5">
      <div className="flex items-center gap-2">
        <FileText size={13} className="shrink-0 text-emerald-400" />
        <h3 className="text-ui font-medium text-slate-200">交付文件</h3>
        <Badge tone="neutral" size="sm">{files.length}</Badge>
        {note && <span className="truncate text-micro text-slate-500">{note}</span>}
      </div>
      <ul className="mt-1.5 flex flex-col gap-1">
        {files.map((f) => (
          <li key={f.path} className="flex items-center gap-2 rounded border border-slate-800/60 px-2 py-1">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-caption text-slate-200" title={f.path}>
                {f.title || f.path.split('/').pop() || f.path}
              </span>
              <span className="block truncate font-mono text-micro text-slate-500" title={f.path}>
                {f.path}
              </span>
            </span>
            <Button
              type="button" size="xs" variant="ghost" data-testid={`present-copy-${f.path}`}
              onClick={() => copy(f.path)} title="复制路径"
            >
              {copied === f.path ? <Check size={11} /> : <Copy size={11} />}
              复制
            </Button>
            <Button
              type="button" size="xs" variant="ghost" data-testid={`present-reveal-${f.path}`}
              onClick={() => revealWorkspace()} title="在文件管理器中显示会话工作区"
            >
              <FolderOpen size={11} />
              显示
            </Button>
          </li>
        ))}
      </ul>
      {revealError && <p className="mt-1 text-micro text-rose-400">{revealError}</p>}
      <p className="mt-1 text-micro text-slate-600">
        卡片只声明路径，不读取文件内容；「显示」打开的是本会话工作区目录。
      </p>
    </Card>
  );
}

