/**
 * ChangeCard —— 本轮变更文件卡（E1-3b）
 * ------------------------------------------------
 * 位置：会话页消息区末尾（交付卡上方）。数据：store.changes（GET /api/changes）。
 *
 * 语义：一轮里改过哪些文件、逐文件 diff（**复用 E0-3 检查点**，不新增采集）。
 * 检查点是 workspace 级的 → 后端回 `scope`：`workspace` 时卡上明确写出来，
 * 不假装"这些改动都属于本会话"。diff 逐文件截断，截断就标出来，不静默丢。
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight, GitCompare } from 'lucide-react';
import { Badge, Button, Card } from '@/components/ui';
import type { ChangedFile, ChangesState } from '@/lib/changesApi';

const STATUS_LABEL: Record<string, string> = {
  M: '修改', A: '新增', '?': '未跟踪', D: '删除', R: '重命名', C: '复制', U: '冲突',
};

function statusText(status: string): string {
  const key = (status || '').trim().charAt(0).toUpperCase();
  return STATUS_LABEL[key] || status || '变更';
}

function FileRow({ file }: { file: ChangedFile }) {
  const [open, setOpen] = useState(false);
  const hasDiff = !!file.diff;
  return (
    <li className="rounded border border-slate-800/60">
      <div className="flex items-center gap-2 px-2 py-1">
        <Button
          type="button" size="xs" variant="ghost"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? '收起 diff' : '展开 diff'}
          data-testid={`change-toggle-${file.path}`}
          className="shrink-0 p-0.5"
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </Button>
        <Badge tone={file.new_file ? 'success' : 'neutral'} size="sm">{statusText(file.status)}</Badge>
        <span className="min-w-0 flex-1 truncate font-mono text-micro text-slate-300" title={file.path}>
          {file.path}
        </span>
        <span className="shrink-0 font-mono text-micro text-emerald-400">+{file.added}</span>
        <span className="shrink-0 font-mono text-micro text-rose-400">-{file.removed}</span>
        {file.binary && <Badge tone="warning" size="sm">二进制</Badge>}
      </div>
      {open && (
        <div className="border-t border-slate-800/60 px-2 py-1">
          {hasDiff ? (
            <pre
              data-testid={`change-diff-${file.path}`}
              className="max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-micro text-slate-400"
            >
              {file.diff}
            </pre>
          ) : (
            <p className="text-micro text-slate-500">
              {file.new_file
                ? '新增文件：`git diff` 里没有块，检查点已整文件捕获内容。'
                : '该文件没有可显示的 diff（二进制或未捕获）。'}
            </p>
          )}
          {file.truncated && <p className="text-micro text-amber-500/80">diff 已截断（只显示前一段）</p>}
        </div>
      )}
    </li>
  );
}

export function ChangeCard({ changes }: { changes: ChangesState }) {
  const cp = changes.checkpoint;
  if (!cp) return null;
  const files = changes.files || [];
  if (files.length === 0) return null;
  return (
    <Card data-testid="change-card" className="mt-1 border-slate-700/60 bg-slate-900/40 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <GitCompare size={13} className="shrink-0 text-sky-400" />
        <h3 className="text-ui font-medium text-slate-200">本轮变更</h3>
        <Badge tone="neutral" size="sm">{files.length} 个文件</Badge>
        <span className="font-mono text-micro text-emerald-400" data-testid="change-added">+{changes.counts.added}</span>
        <span className="font-mono text-micro text-rose-400" data-testid="change-removed">-{changes.counts.removed}</span>
        <span className="truncate text-micro text-slate-500">
          {cp.trigger_tool ? `触发：${cp.trigger_tool}` : ''}
          {cp.created_at ? ` · ${String(cp.created_at).replace('T', ' ')}` : ''}
        </span>
      </div>
      <ul className="mt-1.5 flex flex-col gap-1">
        {files.map((f) => <FileRow key={f.path} file={f} />)}
      </ul>
      {changes.scope !== 'session' && (
        <p className="mt-1 text-micro text-slate-600" data-testid="change-scope-note">
          {changes.note || '工作区级快照：可能包含本会话之外的改动'}
        </p>
      )}
      {changes.counts.truncated > 0 && (
        <p className="mt-1 text-micro text-amber-500/80">
          {changes.counts.truncated} 个文件的 diff 被截断
        </p>
      )}
    </Card>
  );
}

export default ChangeCard;
