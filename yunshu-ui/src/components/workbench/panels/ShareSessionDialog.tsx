/**
 * 会话分享对话框（E4-5 第六批 LiveShare）：建链接 / 复制 / 撤销。
 *
 * 【安全】令牌只在创建响应里出现一次，页面只把它拼进输入框供复制；
 * 读面用 X-LiveShare-Token 头（不放进 URL，避免 Referer/反代日志留痕）。
 * 【开关关】如实显示「未启用」，不给创建入口。
 */
import { useEffect, useState } from 'react';
import { Copy, Link2, Trash2 } from 'lucide-react';
import { Badge, Button, Input, ModalBase, Notice, Select } from '@/components/ui';
import { request } from '@/lib/apiClient';
import { sessionShares } from '@/api/endpoints';

export interface ShareRow {
  created_at: string;
  expires_at: string;
  revoked: boolean;
  active: boolean;
}

export interface ShareStatus {
  ok: boolean;
  code?: string;
  error?: string;
  enabled?: boolean;
  shares?: ShareRow[];
  default_ttl_seconds?: number;
}

export interface CreatedShare {
  ok: boolean;
  code?: string;
  error?: string;
  token?: string;
  url_path?: string;
  expires_at?: string;
  ttl_seconds?: number;
}

const TTL_OPTIONS = [
  { value: '3600', label: '1 小时' },
  { value: '86400', label: '24 小时（默认）' },
  { value: '604800', label: '7 天（上限）' },
];

/** 分享链接：同源 + 当前路径 + hash 路由（令牌在 # 之后，不进服务器日志） */
export function shareLink(token: string): string {
  if (typeof window === 'undefined') return '#/live/' + token;
  return window.location.origin + window.location.pathname + '#/live/' + token;
}

export function ShareSessionDialog({
  open,
  onClose,
  sessionId,
  sessionTitle,
}: {
  open: boolean;
  onClose: () => void;
  sessionId: string | null;
  sessionTitle?: string;
}) {
  const [status, setStatus] = useState<ShareStatus | null>(null);
  const [created, setCreated] = useState<CreatedShare | null>(null);
  const [ttl, setTtl] = useState('86400');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) {
      setCreated(null);
      setCopied(false);
      setError('');
      return;
    }
    setCreated(null);
    setCopied(false);
    setError('');
    if (!sessionId) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sessionId]);

  async function load() {
    if (!sessionId) return;
    setBusy(true);
    try {
      const body = await request<ShareStatus>(sessionShares(sessionId));
      setStatus(body);
      if (body.ok === false) setError(body.error || '读取分享状态失败');
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取分享状态失败');
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    if (!sessionId) return;
    setBusy(true);
    setError('');
    try {
      const body = await request<CreatedShare>(sessionShares(sessionId), {
        method: 'POST',
        body: { ttl_seconds: Number(ttl) },
      });
      if (body.ok === false) {
        setError(body.error || '创建分享失败');
        return;
      }
      setCreated(body);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建分享失败');
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!sessionId) return;
    setBusy(true);
    setError('');
    try {
      const body = await request<{ ok: boolean; revoked?: number; error?: string }>(
        sessionShares(sessionId),
        { method: 'DELETE' },
      );
      if (body.ok === false) {
        setError(body.error || '撤销失败');
        return;
      }
      setCreated(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : '撤销失败');
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!created?.token) return;
    const link = shareLink(created.token);
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const disabled = status?.ok === false && status?.code === 'disabled';
  const activeCount = (status?.shares || []).filter((row) => row.active).length;

  return (
    <ModalBase
      open={open}
      onClose={onClose}
      title="分享这个会话"
      width="max-w-lg"
      testId="session-share-dialog"
      closeButton
    >
      <div className="flex flex-col gap-3">
        <p className="text-caption text-muted-foreground">
          生成一个只读链接：对方**无需登录**即可看这段对话（含之后的新消息），
          随时可撤销，默认 24 小时过期。
          {sessionTitle ? '当前会话：' : ''}
          {sessionTitle ? <span className="text-foreground">{sessionTitle}</span> : null}
        </p>

        {error ? (
          <Notice tone="danger" size="sm" data-testid="session-share-error">
            {error}
          </Notice>
        ) : null}

        {disabled ? (
          <Notice tone="warning" size="sm" data-testid="session-share-disabled">
            会话分享未启用（CP_LIVESHARE_ENABLED=0）
          </Notice>
        ) : (
          <div className="flex items-end gap-2">
            <Select
              aria-label="有效期"
              size="xs"
              className="w-40"
              value={ttl}
              onChange={(v) => setTtl(v)}
              options={TTL_OPTIONS}
            />
            <Button
              type="button"
              variant="primary"
              size="xs"
              data-testid="session-share-create"
              disabled={busy || !sessionId}
              onClick={() => void create()}
            >
              <Link2 size={11} className="mr-1" />
              生成分享链接
            </Button>
            <Badge tone="neutral" size="sm" className="ml-auto font-mono" data-testid="session-share-count">
              生效中 {activeCount}
            </Badge>
          </div>
        )}

        {created?.token ? (
          <div className="flex flex-col gap-1">
            <span className="text-micro text-muted-foreground">
              链接只显示一次，复制发给对方（过期 {created.expires_at || '—'}）
            </span>
            <div className="flex items-center gap-2">
              <Input
                readOnly
                aria-label="分享链接"
                data-testid="session-share-link"
                value={shareLink(created.token)}
                size="xs"
                className="min-w-0 flex-1"
                inputClassName="font-mono text-micro"
              />
              <Button
                type="button"
                variant="default"
                size="xs"
                data-testid="session-share-copy"
                onClick={() => void copy()}
              >
                <Copy size={11} className="mr-1" />
                {copied ? '已复制' : '复制'}
              </Button>
            </div>
          </div>
        ) : null}

        {!disabled && activeCount > 0 ? (
          <div className="flex flex-col gap-1">
            <span className="text-micro text-muted-foreground">生效中的分享</span>
            <ul className="flex max-h-40 flex-col gap-1 overflow-auto pr-1">
              {(status?.shares || [])
                .filter((row) => row.active)
                .map((row) => (
                  <li
                    key={row.created_at + row.expires_at}
                    data-testid="session-share-row"
                    className="flex items-center gap-2 rounded-md border border-slate-800/70 bg-slate-900/40 px-2 py-1"
                  >
                    <span className="min-w-0 flex-1 truncate font-mono text-micro text-slate-400">
                      {row.created_at} → {row.expires_at}
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      data-testid="session-share-revoke"
                      disabled={busy}
                      onClick={() => void revoke()}
                    >
                      <Trash2 size={11} className="mr-1" />
                      全部撤销
                    </Button>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
      </div>
    </ModalBase>
  );
}
