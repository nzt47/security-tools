/**
 * # 会话引用菜单（E3-3 尾巴）：输入 `#关键词` 列出会话，选中插入带 session_id 的引用行。
 *
 * 【为什么插一行带 id 的说明而不是链接】模型消费的是文本：给它 session_id=... 它就能用
 * session_search / session_trace 精确检索那个会话；给个 UI 链接对它毫无意义。
 *
 * 【为什么用 # 而不是 @】@ 已给文件引用（E3-3），两个入口分开才不用猜；# 在中文输入里
 * 也更少与正常文本冲突。
 */
import { useEffect, useRef, useState } from 'react';
import { MessagesSquare } from 'lucide-react';
import { Button } from '@/components/ui';
import { SESSIONS } from '@/api/endpoints';
import { authHeader } from '../../../lib/apiToken';

interface SessionHit {
  id: string;
  title: string;
}

const MAX_HITS = 8;

/** 取末尾 #片段（无空格）；没有则 null */
export function extractSessionQuery(text: string): string | null {
  const m = /#([^\s#]*)$/.exec(text);
  return m ? m[1] : null;
}

/** 选中会话时插入的引用行（模型可据此调 session_search/session_trace） */
export function sessionMentionLine(title: string, id: string): string {
  return '【引用会话】' + (title || '(未命名)') + '（session_id=' + id + '）';
}

/** 把末尾 #片段替换成引用行 */
export function applySessionMention(text: string, title: string, id: string): string {
  const idx = text.lastIndexOf('#');
  const head = idx < 0 ? text : text.slice(0, idx);
  return head + sessionMentionLine(title, id) + ' ';
}

export function SessionMentionMenu({
  query,
  onPick,
  onClose,
}: {
  query: string;
  onPick: (hit: SessionHit) => void;
  onClose: () => void;
}) {
  const [hits, setHits] = useState<SessionHit[]>([]);
  const [loading, setLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (query.trim().length < 1) {
      setHits([]);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setLoading(true);
      try {
        // 会话列表端点没有搜索参数 ⇒ 取回来按标题/ id 本地过滤（会话数量级很小）
        const res = await fetch(SESSIONS, { headers: authHeader() });
        const body = (await res.json().catch(() => null)) as
          | { sessions?: { id?: string; title?: string }[] }
          | null;
        const q = query.trim().toLowerCase();
        const list = (body && body.sessions) || [];
        setHits(
          list
            .filter((s) => s && s.id)
            .map((s) => ({ id: String(s.id), title: String(s.title || '') }))
            .filter((s) => !q || s.title.toLowerCase().includes(q) || s.id.toLowerCase().includes(q))
            .slice(0, MAX_HITS),
        );
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query]);

  if (query.trim().length < 1) return null;

  return (
    <div className="wb-mention-menu" role="listbox" aria-label="会话引用建议">
      {hits.length === 0 ? (
        <div className="px-2 py-1 text-caption text-slate-500">
          {loading ? '读取会话…' : '无匹配会话（取消 # 可继续手打）'}
        </div>
      ) : (
        hits.map((hit) => (
          <Button
            key={hit.id}
            type="button"
            variant="plain"
            size="bare"
            role="option"
            className="wb-mention-item"
            title={hit.id}
            onClick={() => onPick(hit)}
          >
            <MessagesSquare size={11} />
            <span className="min-w-0 flex-1 truncate text-left">{hit.title || hit.id}</span>
          </Button>
        ))
      )}
      <Button
        type="button"
        variant="plain"
        size="bare"
        className="wb-mention-close"
        aria-label="关闭会话引用建议"
        onClick={onClose}
      >
        关闭
      </Button>
    </div>
  );
}
