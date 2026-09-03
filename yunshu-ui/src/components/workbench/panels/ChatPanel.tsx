/**
 * ChatPanel —— 主工作区对话流
 * ------------------------------------------------
 * 数据流：用户输入 → store.sendMessage → POST /api/chat/stream（真实 SSE）
 *         → 逐 chunk 写入 → MessageItem 逐块重渲染 + Framer Motion 入场动画
 * 停止：store.stopStreaming 通过 AbortController 中断 fetch 流。
 * 日志：订阅 store 流式事件，打印分片序号/间隔/乱序/断流告警（本文件下方 useEffect）。
 */
import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Sparkles, Square } from 'lucide-react';
import { subscribeStreamLog, useLayoutStore } from '../../../stores/useLayoutStore';
import { MessageItem } from '../chat/MessageItem';
import { MessageInput } from '../chat/MessageInput';

const SUGGESTIONS = [
  '用流式渲染实现一个聊天面板',
  '帮我规划一个知识检索任务',
  '云枢的监控体系如何运作？',
];

/** 历史问话跳转高亮持续时间（ms），到时由本组件清空 store 的 highlightMsgId */
const HIGHLIGHT_CLEAR_MS = 2000;

export function ChatPanel() {
  const messages = useLayoutStore((s) => s.messages);
  const streaming = useLayoutStore((s) => s.streaming);
  const highlightMsgId = useLayoutStore((s) => s.highlightMsgId);
  const sendMessage = useLayoutStore((s) => s.sendMessage);
  const stopStreaming = useLayoutStore((s) => s.stopStreaming);

  const [input, setInput] = useState('');
  // 斜杠命令注册表（已发布技能 → /skill:<id>，输入 / 时提示）
  interface SlashCmd { token?: string; id?: string; name?: string; description?: string; content_type?: string; category?: string; version?: string }
  const typeLabel = (c: SlashCmd): string => {
    const ct = String(c.content_type ?? '').toUpperCase();
    return ct.slice(0, 6) || 'SKILL';
  };
  const [cmds, setCmds] = useState<SlashCmd[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetch('/api/skills-mgmt/slash-commands')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d?.commands) setCmds(d.commands); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const slashPart = input.startsWith('/') ? input.slice(1).trim().toLowerCase() : '';
  const slashHits = slashPart
    ? cmds.filter((k) =>
        (k.token ?? '').slice(1).toLowerCase().startsWith(slashPart)
        || (k.id ?? '').toLowerCase().startsWith(slashPart)
        || String(k.name ?? '').toLowerCase().includes(slashPart)).slice(0, 8)
    : [];

  const expandSlash = (raw: string): string => {
    const m = raw.trim().match(/^(\/skill:[a-z0-9_-]+)(?:\s+([\s\S]*))?$/i);
    if (m) {
      const cmd = cmds.find((k) => (k.token ?? '').toLowerCase() === m[1].toLowerCase());
      if (cmd) {
        const task = (m[2] ?? '').trim();
        return `请调用已注册技能「${cmd.name ?? cmd.id}」（${cmd.id}）。${task ? `任务：${task}` : '请按该技能用途处理。'}`;
      }
    }
    return raw.trim();
  };

  // ── 斜杠下拉键盘导航（↑↓ 移动高亮 · Enter 选中并发送展开指令 · Tab 仅插入）──
  const [hi, setHi] = useState(0);
  const hiSafe = Math.min(hi, Math.max(0, slashHits.length - 1));
  useEffect(() => { setHi(0); }, [slashPart]);
  const pickSlash = (c: SlashCmd, send: boolean) => {
    const token = c.token ?? '';
    setInput(`${token} `);
    setHi(0);
    if (send) {
      sendMessage(expandSlash(`${token} `));
      setInput('');
    }
  };
  const onSlashKey = (e: React.KeyboardEvent) => {
    if (slashHits.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHi((h) => Math.min(h + 1, slashHits.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, 0));
    } else if (e.key === 'Tab' || e.key === 'Enter') {
      e.preventDefault(); // 阻止默认发送/失焦：下拉打开时由这里接管
      pickSlash(slashHits[hiSafe], e.key === 'Enter');
    }
  };
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // 最后一条消息内容：内容增长时触底滚动（独立变量便于依赖数组静态检查）
  const lastMsgContent = messages[messages.length - 1]?.content;

  // 流式内容增长 / 新消息 → 平滑滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, lastMsgContent]);

  // 历史问话跳转定位：滚动到目标消息（data-mid 锚点）并触发 wb-msg-flash 高亮，
  // 动画结束后清空 store 标记，避免下一次挂载重复闪烁。
  useEffect(() => {
    if (!highlightMsgId) return;
    const node = scrollRef.current?.querySelector<HTMLElement>(`[data-mid="${highlightMsgId}"]`);
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const t = setTimeout(() => {
      useLayoutStore.getState().setHighlightMsg(null);
    }, HIGHLIGHT_CLEAR_MS);
    return () => clearTimeout(t);
  }, [highlightMsgId]);

  // ─── 流式过程日志（排查断流 / 乱序 / 丢包） ───
  // 订阅 store 的流式事件，集中打印：分片序号、长度、累计、间隔、汇总统计。
  useEffect(() => {
    const trace = { lastSeq: -1, lastChunkTs: 0, chunkCount: 0, totalChars: 0, startTs: 0 };
    return subscribeStreamLog((e) => {
      switch (e.kind) {
        case 'send': {
          trace.lastSeq = -1;
          trace.chunkCount = 0;
          trace.totalChars = 0;
          trace.startTs = e.ts;
          trace.lastChunkTs = 0; // 防止跨会话首个 chunk 用上次的时间戳误报断流
          console.group(`%c[云枢·SSE] 发送 #${e.streamId}`, 'color:#22d3ee');
          console.debug('[云枢·SSE] 请求:', { streamId: e.streamId, question: e.detail, ts: e.ts });
          break;
        }
        case 'thinking':
          console.debug('[云枢·SSE] [思考]', { title: e.title, status: e.status, detail: e.detail, ts: e.ts });
          break;
        case 'chunk': {
          const gapMs = trace.lastChunkTs ? e.ts - trace.lastChunkTs : 0;
          trace.chunkCount += 1;
          trace.totalChars += e.text?.length ?? 0;
          // 断流检测：距上个分片超过 3s → 警告
          if (gapMs > 3000) {
            console.warn(`[云枢·SSE] ⚠ 疑似断流：距上个 chunk 达 ${gapMs}ms`, { seq: e.seq, ts: e.ts });
          }
          // 乱序 / 丢包检测（依赖后端 chunk 的 seq 序号）
          if (e.seq !== undefined) {
            if (e.seq <= trace.lastSeq) {
              console.warn(`[云枢·SSE] ⚠ 乱序/重复：seq=${e.seq}（上次 ${trace.lastSeq}）`, { ts: e.ts });
            } else if (trace.lastSeq >= 0 && e.seq - trace.lastSeq > 1) {
              console.warn(`[云枢·SSE] ⚠ 疑似丢包：seq ${trace.lastSeq} → ${e.seq} 跳变`, { ts: e.ts });
            }
            trace.lastSeq = e.seq;
          }
          console.debug(
            `[云枢·SSE] chunk#${trace.chunkCount}`,
            { seq: e.seq, len: e.text?.length, accumulated: e.accumulated, gapMs, ts: e.ts },
          );
          trace.lastChunkTs = e.ts;
          break;
        }
        case 'done': {
          const durationMs = e.ts - trace.startTs;
          const rate = durationMs > 0 ? ((trace.totalChars / durationMs) * 1000).toFixed(1) : '-';
          console.info('[云枢·SSE] ✅ 流式完成', {
            chunks: trace.chunkCount,
            totalChars: trace.totalChars,
            durationMs,
            rateCharsPerSec: rate,
          });
          console.groupEnd();
          break;
        }
        case 'error':
          console.error('[云枢·SSE] ❌ 流式错误', { detail: e.detail, accumulated: e.accumulated, ts: e.ts });
          console.groupEnd();
          break;
        case 'abort':
          console.warn('[云枢·SSE] ⏹ 用户中止', { accumulated: e.accumulated, ts: e.ts });
          console.groupEnd();
          break;
      }
    });
  }, []);

  const handleSend = () => {
    if (!input.trim() || streaming) return;
    sendMessage(expandSlash(input));
    setInput('');
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 消息区 */}
      <div ref={scrollRef} className="wb-chat-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="mx-auto mt-[12vh] flex max-w-sm flex-col items-center gap-4 text-center"
          >
            <div className="wb-logo-badge">
              <Sparkles size={20} />
            </div>
            <p className="text-sm text-slate-400">
              云枢工作台就绪。向 AI 提问，观察右侧思考过程与流式输出。
            </p>
            <div className="flex flex-col gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="wb-chip"
                  onClick={() => sendMessage(s)}
                  disabled={streaming}
                >
                  {s}
                </button>
              ))}
            </div>
          </motion.div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            <AnimatePresence initial={false}>
              {messages.map((m) => (
                <MessageItem key={m.id} message={m} highlighted={m.id === highlightMsgId} />
              ))}
            </AnimatePresence>
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* 输入区 */}
      <div className="wb-input-pad border-t border-slate-800/60 px-4 py-3" onKeyDownCapture={onSlashKey}>
        {streaming && (
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs text-cyan-400/80">
              <span className="wb-pulse-dot" />
              正在生成…
            </span>
            <button
              type="button"
              onClick={stopStreaming}
              className="wb-stop-btn"
            >
              <Square size={11} fill="currentColor" />
              停止生成
            </button>
          </div>
        )}
        {slashHits.length > 0 && (
          <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/95 p-1 shadow-xl">
            <div className="flex items-center justify-between px-2 pb-1 pt-0.5">
              <span className="text-[9px] uppercase tracking-wider text-slate-500">已注册技能斜杠（↑↓ 选择 · Enter 选中即发送 · Tab 仅插入）</span>
              {slashHits.length > 1 && <span className="font-mono text-[9px] text-cyan-400/70">{hiSafe + 1}/{slashHits.length}</span>}
            </div>
            {slashHits.map((c, idx) => (
              <button
                key={c.token}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); pickSlash(c, false); }}
                onMouseEnter={() => setHi(idx)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-cyan-500/10 ${idx === hiSafe ? 'bg-cyan-500/15 ring-1 ring-cyan-600/50' : ''}`}
              >
                <span className="shrink-0 font-mono text-[11px] text-cyan-300">{c.token}</span>
                <span className="shrink-0 rounded border border-cyan-800/50 bg-cyan-500/10 px-1 text-[9px] font-medium text-cyan-300" title={`类型 ${c.content_type ?? ''}`}>{typeLabel(c)}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-slate-200">{c.name}</span>
                <span className="shrink-0 text-[9px] text-slate-600">{c.version ? `v${c.version}` : ''}</span>
                <span className="shrink-0 text-[9px] text-slate-600">{c.category ? `[${c.category}]` : ''}</span>
                <span className="hidden max-w-40 min-w-0 truncate text-[10px] text-slate-500 sm:inline">{c.description}</span>
              </button>
            ))}
          </div>
        )}
        <MessageInput
          value={input}
          onChange={setInput}
          onSend={handleSend}
          disabled={streaming}
        />
      </div>
    </div>
  );
}
