/**
 * @ 文件引用菜单（E3-3）：输入 `@关键词` 时列出匹配文件，选中插入相对路径。
 *
 * 【为什么复用既有 /api/filesystem/search】不新增路由（避免 route_ui_coverage/意图台账的
 *   额外债务），也不在前端造第二套文件索引：后端那份搜索本来就是模型 read_file 的同一口径。
 *
 * 【失败姿态】搜索失败只显示「无匹配」并可继续手打路径；绝不把菜单卡住（autocomplete 是
 *   加速器，不是必经之路）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { FileText } from 'lucide-react';
import { Button } from '@/components/ui';
import { FILESYSTEM_SEARCH } from '@/api/endpoints';
import { authHeader } from '../../../lib/apiToken';

export interface FileHit {
  path: string;
  name: string;
}

const MAX_HITS = 8;

/** 从输入框当前文本里取「正在输入的 @ 片段」（取最后一个 @ 之后到行尾/空格） */
export function extractMentionQuery(text: string): string | null {
  const m = /@([^\s@]*)$/.exec(text);
  return m ? m[1] : null;
}

/** 用选中路径替换文本末尾的 @片段 */
export function applyMention(text: string, path: string): string {
  const idx = text.lastIndexOf('@');
  if (idx < 0) return text + path + ' ';
  return text.slice(0, idx) + '@' + path + ' ';
}

export function FileMentionMenu({
  query,
  onPick,
  onClose,
}: {
  query: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [hits, setHits] = useState<FileHit[]>([]);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (query.trim().length < 2) {
      setHits([]);
      return;
    }
    // 防抖 350ms：文件搜索要遍历目录，不要每敲一个字符都打一次
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setLoading(true);
      try {
        const url =
          FILESYSTEM_SEARCH +
          '?pattern=' + encodeURIComponent('*' + query.trim() + '*') +
          '&root_path=' + encodeURIComponent('.');
        const res = await fetch(url, { headers: authHeader() });
        const body = (await res.json().catch(() => null)) as
          | { ok?: boolean; results?: { path?: string; name?: string }[] }
          | null;
        const list = (body && body.ok && body.results) || [];
        setHits(
          list
            .slice(0, MAX_HITS)
            .map((r) => ({ path: String(r.path || ''), name: String(r.name || '') }))
            .filter((r) => r.path),
        );
        setActive(0);
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 350);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query]);

  const list = useMemo(() => hits, [hits]);

  if (query.trim().length < 2) return null;

  return (
    <div className="wb-mention-menu" role="listbox" aria-label="文件引用建议">
      {list.length === 0 ? (
        <div className="px-2 py-1 text-caption text-slate-500">
          {loading ? '搜索中…' : '无匹配（可直接手打路径）'}
        </div>
      ) : (
        list.map((hit, i) => (
          <Button
            key={hit.path}
            type="button"
            variant="plain"
            size="bare"
            role="option"
            aria-selected={i === active}
            className={'wb-mention-item' + (i === active ? ' is-active' : '')}
            title={hit.path}
            onClick={() => onPick(hit.path)}
          >
            <FileText size={11} />
            <span className="min-w-0 flex-1 truncate text-left">{hit.name}</span>
            <span className="shrink-0 text-micro text-slate-500">{hit.path}</span>
          </Button>
        ))
      )}
      <Button
        type="button"
        variant="plain"
        size="bare"
        className="wb-mention-close"
        aria-label="关闭文件引用建议"
        onClick={onClose}
      >
        关闭
      </Button>
    </div>
  );
}
