/**
 * 会话导入对话框（E4-5 第四批）：选导出文件 → 只读预览 → 显式确认导入。
 *
 * 【为什么先预览】导入是往会话库里**追加**会话与消息：先看清楚"几个会话、多少条、
 * 哪些已经导过（重复）"，再点确认；预览本身零写入（后端 apply 分支另有写开关）。
 *
 * 【失败姿态】格式不支持 / 写路径关 / 并发冲突都只显示后端给的稳定中文短句，
 * 不把 HTTP 细节糊在界面上；开关关闭时如实显示"未启用"，不假装成功。
 */
import { useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { Badge, Button, Checkbox, Input, ModalBase, Notice } from '@/components/ui';
import { SESSIONS_IMPORT } from '@/api/endpoints';
import { authHeader } from '../../../lib/apiToken';

export interface ImportPreviewRow {
  title: string;
  messages: number;
  chars: number;
  first_ts: string;
  last_ts: string;
  fingerprint: string;
  duplicate: boolean;
}

export interface ImportPreview {
  ok?: boolean;
  code?: string;
  error?: string;
  write_enabled?: boolean;
  filename?: string;
  format?: string;
  session_count?: number;
  duplicates?: number;
  sessions?: ImportPreviewRow[];
  stats?: Record<string, number>;
  allowed_formats?: string[];
}

export interface ImportResult {
  ok?: boolean;
  code?: string;
  error?: string;
  filename?: string;
  created?: { id: string; title: string; messages: number }[];
  created_count?: number;
  skipped_duplicates?: number;
}

/** 上传一个文件走导入端点（multipart；默认只预览）。 */
export async function postSessionImport(
  file: File,
  mode: 'preview' | 'apply',
  confirm = false,
): Promise<ImportPreview | ImportResult> {
  const form = new FormData();
  form.append('file', file);
  form.append('mode', mode);
  if (confirm) form.append('confirm', '1');
  const res = await fetch(SESSIONS_IMPORT, {
    method: 'POST',
    headers: authHeader(),
    body: form,
  });
  const body = (await res.json().catch(() => null)) as ImportPreview | ImportResult | null;
  if (!body) throw new Error('服务返回无法解析');
  return body;
}

export function SessionImportDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported?: (sessionIds: string[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (open) return;
    setFile(null);
    setPreview(null);
    setResult(null);
    setBusy(false);
    setConfirmed(false);
    setError('');
  }, [open]);

  async function pick(next: File) {
    setFile(next);
    setPreview(null);
    setResult(null);
    setConfirmed(false);
    setError('');
    setBusy(true);
    try {
      const body = await postSessionImport(next, 'preview');
      if (body.ok === false) {
        setError(body.error || '预览失败');
        return;
      }
      setPreview(body as ImportPreview);
    } catch (e) {
      setError(e instanceof Error ? e.message : '预览失败');
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const body = await postSessionImport(file, 'apply', true);
      if (body.ok === false) {
        setError(body.error || '导入失败');
        return;
      }
      setResult(body as ImportResult);
      const ids = ((body as ImportResult).created ?? []).map((row) => row.id);
      onImported?.(ids);
    } catch (e) {
      setError(e instanceof Error ? e.message : '导入失败');
    } finally {
      setBusy(false);
    }
  }

  const rows = preview?.sessions ?? [];
  const writeEnabled = preview?.write_enabled === true;
  const canApply = writeEnabled && !!file && !busy && confirmed && !result;

  return (
    <ModalBase
      open={open}
      onClose={onClose}
      title="导入会话"
      width="max-w-2xl"
      testId="session-import-dialog"
      closeButton
      scrollable
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="session-import-apply"
            disabled={!canApply}
            onClick={() => void apply()}
          >
            {busy && !result ? '导入中…' : '确认导入'}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-caption text-muted-foreground">
          支持云枢会话导出（yunshu.session.v1）与 DeepSeek 会话导出（deepseek.export.v1），
          文件为 .json 或 .zip。默认只预览，不会写任何会话。
        </p>
        <div className="flex items-center gap-2">
          <Input
            ref={inputRef}
            variant="bare"
            type="file"
            accept=".json,.zip"
            aria-label="选择会话导出文件"
            data-testid="session-import-file"
            inputClassName="text-caption"
            onChange={(e) => {
              const next = e.target.files?.[0];
              if (next) void pick(next);
            }}
            disabled={busy}
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            <Upload size={13} className="mr-1" />
            选择文件
          </Button>
        </div>

        {error ? (
          <Notice tone="danger" size="sm" data-testid="session-import-error">
            {error}
          </Notice>
        ) : null}

        {result ? (
          <Notice tone="success" size="sm" data-testid="session-import-result">
            已导入 {(result.created ?? []).length} 个会话
            {result.skipped_duplicates ? `（跳过重复 ${result.skipped_duplicates} 个）` : ''}
          </Notice>
        ) : null}

        {preview ? (
          <div className="flex flex-col gap-2" data-testid="session-import-preview">
            <div className="flex flex-wrap items-center gap-2 text-caption text-muted-foreground">
              <Badge tone="neutral" size="sm" className="font-mono">
                {preview.format || '未知格式'}
              </Badge>
              <span data-testid="session-import-counts">
                {preview.session_count ?? 0} 个会话 · {preview.duplicates ?? 0} 个已导入
              </span>
            </div>
            {!writeEnabled ? (
              <Notice tone="warning" size="sm" data-testid="session-import-write-disabled">
                写路径未开启（CP_SESSION_IMPORT_WRITE_ENABLED=0）—— 当前只能预览
              </Notice>
            ) : (
              <Checkbox
                data-testid="session-import-confirm"
                label="我确认把这些会话写入会话库（重复的会自动跳过）"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                disabled={busy || !!result}
              />
            )}
            <ul className="flex max-h-64 flex-col gap-1 overflow-auto pr-1">
              {rows.map((row) => (
                <li
                  key={row.fingerprint}
                  data-testid="session-import-row"
                  className="flex items-center gap-2 rounded-md border border-slate-800/70 bg-slate-900/40 px-2 py-1"
                >
                  <span className="min-w-0 flex-1 truncate text-caption text-slate-200" title={row.title}>
                    {row.title}
                  </span>
                  {row.duplicate ? (
                    <Badge tone="warning" size="sm">
                      已导入
                    </Badge>
                  ) : null}
                  <span className="shrink-0 font-mono text-micro text-slate-500">
                    {row.messages} 条
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </ModalBase>
  );
}
