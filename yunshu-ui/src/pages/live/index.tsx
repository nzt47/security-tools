/**
 * LiveShare 只读页（E4-5 第六批）：#/live/<token>
 *
 * 【为什么令牌在 hash 里】HashRouter 的 # 之后不会发给服务器（不进访问日志/Referer）；
 * 页面用 X-LiveShare-Token 头去拉 read-only 分页，每 2s 轮询增量（after=next_after）。
 * 【失败姿态】令牌无效/过期/撤销/功能关 一律 404 ⇒ 同一句「链接不存在或已失效」，
 * 不给枚举者任何区别。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Eye } from 'lucide-react';
import { Badge, Notice } from '@/components/ui';
import { LIVESHARE_MESSAGES } from '@/api/endpoints';

export interface LiveMessage {
  seq: number;
  role: string;
  content: string;
  chars: number;
  truncated: boolean;
  timestamp: string;
}

export interface LiveBody {
  ok: boolean;
  code?: string;
  error?: string;
  title?: string;
  expires_at?: string;
  messages?: LiveMessage[];
  next_after?: number;
  total?: number;
  truncated?: boolean;
}

/** 轮询间隔（毫秒）：只读页，2s 足够；测试可直接改这个导出常量 */
export const LIVE_POLL_INTERVAL_MS = 2000;

export function LiveSharePage() {
  const { token = '' } = useParams<{ token: string }>();
  const [title, setTitle] = useState('');
  const [rows, setRows] = useState<LiveMessage[]>([]);
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const cursor = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const fetchPage = useCallback(async () => {
    if (!token) {
      setInvalid(true);
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(
        LIVESHARE_MESSAGES + '?after=' + cursor.current + '&limit=50',
        { headers: { 'X-LiveShare-Token': token } },
      );
      const body = (await res.json().catch(() => null)) as LiveBody | null;
      if (!res.ok || !body || body.ok === false) {
        setInvalid(true);
        setLoading(false);
        return;
      }
      if (body.title) setTitle(body.title);
      const incoming = body.messages ?? [];
      if (incoming.length) {
        setRows((prev) => {
          const seen = new Set(prev.map((row) => row.seq));
          return [...prev, ...incoming.filter((row) => !seen.has(row.seq))];
        });
      }
      cursor.current = body.next_after ?? cursor.current;
      setInvalid(false);
      setError('');
      setLoading(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : '读取失败');
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    cursor.current = 0;
    setRows([]);
    setTitle('');
    setInvalid(false);
    setLoading(true);
    void fetchPage();
    const timer = setInterval(() => {
      void fetchPage();
    }, LIVE_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [fetchPage]);

  useEffect(() => {
    // jsdom / 老宿主没有 scrollIntoView：缺了也只是不自动滚到底，不该让页面崩
    bottomRef.current?.scrollIntoView?.({ block: 'end' });
  }, [rows.length]);

  return (
    <div className="flex h-screen flex-col bg-slate-950 text-slate-100" data-testid="live-share-page">
      <header className="flex shrink-0 items-center gap-2 border-b border-slate-800 bg-slate-900/60 px-4 py-2.5">
        <Eye size={14} className="text-cyan-400" />
        <span className="min-w-0 flex-1 truncate text-body font-medium" data-testid="live-share-title">
          {title || '会话分享（只读）'}
        </span>
        <Badge tone="neutral" size="sm">
          只读
        </Badge>
      </header>

      <main className="min-h-0 flex-1 overflow-auto px-4 py-3">
        {invalid ? (
          <Notice tone="warning" size="sm" data-testid="live-share-invalid">
            链接不存在或已失效（可能已过期或被撤销）
          </Notice>
        ) : null}
        {error ? (
          <Notice tone="danger" size="sm" className="mb-2" data-testid="live-share-error">
            读取失败：{error}（会自动重试）
          </Notice>
        ) : null}
        {loading && rows.length === 0 ? (
          <div className="text-caption text-slate-500" data-testid="live-share-loading">
            正在加载…
          </div>
        ) : null}
        <ul className="flex flex-col gap-2">
          {rows.map((row) => (
            <li
              key={row.seq}
              data-testid="live-share-message"
              className="rounded-lg border border-slate-800/80 bg-slate-900/40 px-3 py-2"
            >
              <div className="mb-1 flex items-center gap-2 text-micro text-slate-500">
                <Badge tone={row.role === 'user' ? 'primary' : 'neutral'} size="sm">
                  {row.role === 'user' ? '用户' : row.role === 'assistant' ? '云枢' : row.role}
                </Badge>
                <span className="font-mono">{row.timestamp}</span>
              </div>
              <div className="whitespace-pre-wrap break-words text-body text-slate-200">
                {row.content}
                {row.truncated ? <span className="text-micro text-slate-500">（已截断）</span> : null}
              </div>
            </li>
          ))}
        </ul>
        <div ref={bottomRef} />
      </main>
    </div>
  );
}

export default LiveSharePage;
