/**
 * ChatPanel —— 主工作区对话流
 * ------------------------------------------------
 * 数据流：用户输入 → store.sendMessage → POST /api/chat/stream（真实 SSE）
 *         → 逐 chunk 写入 → MessageItem 逐块重渲染 + Framer Motion 入场动画
 * 停止：store.stopStreaming 通过 AbortController 中断 fetch 流。
 * 日志：订阅 store 流式事件，打印分片序号/间隔/乱序/断流告警（本文件下方 useEffect）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Sparkles, Square } from 'lucide-react';
import { Badge, Button } from '@/components/ui';
import { isHistoryMessageId, subscribeStreamLog, useLayoutStore } from '../../../stores/useLayoutStore';
import { chatStyleVars, useChatPrefsStore } from '../../../stores/useChatPrefsStore';
import { MessageItem, isToolStep } from '../chat/MessageItem';
import { computeTurnMeta, shouldFoldProcess, shouldHideIntermediateReply } from '../chat/transcriptFold';
import { MessageInput } from '../chat/MessageInput';
import { AttachmentPicker, buildAttachmentManifest, type AttachmentMeta } from '../chat/AttachmentPicker';
import { VoiceInputButton } from '../chat/VoiceInputButton';
import { ChatStyleMenu } from '../chat/ChatStyleMenu';
import { ChatModeMenu } from '../chat/ChatModeMenu';
import { PermissionModeMenu } from '../chat/PermissionModeMenu';
import { ApprovalPrompt } from '../chat/ApprovalPrompt';
// E1-1 计划模式：卡片只嵌在会话页（不新增页面/叶子 ⇒ 深链不变）
import { PlanCard } from '../chat/PlanCard';
import { getPlan, setPlanMode } from '@/lib/planApi';
// E1-2d 目标：进度条只嵌会话页（不新增叶子）
import { GoalBar } from '../chat/GoalBar';
import { getGoal } from '@/lib/goalApi';
// E1-3a 交互：提问卡 + 交付卡
import { QuestionCard } from '../chat/QuestionCard';
import { PresentCard } from '../chat/PresentCard';
import { ChangeCard } from '../chat/ChangeCard';
import { getPendingAsk } from '@/lib/askApi';
import { getChanges } from '@/lib/changesApi';
import { authHeader } from '@/lib/apiToken';

import {
  SKILLS_MGMT_SLASH_COMMANDS,
} from '@/api/endpoints';

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
  // ── E1-1 计划模式 ───────────────────────────────────────────────
  const activeSessionId = useLayoutStore((s) => s.activeSessionId);
  const plan = useLayoutStore((s) => s.plan);
  const setPlan = useLayoutStore((s) => s.setPlan);
  const goal = useLayoutStore((s) => s.goal);
  const setGoal = useLayoutStore((s) => s.setGoal);
  const pendingAsk = useLayoutStore((s) => s.pendingAsk);
  const setPendingAsk = useLayoutStore((s) => s.setPendingAsk);
  const presented = useLayoutStore((s) => s.presented);
  const changes = useLayoutStore((s) => s.changes);
  const setChanges = useLayoutStore((s) => s.setChanges);
  const changeScope = useLayoutStore((s) => s.changeScope);
  const setChangeScope = useLayoutStore((s) => s.setChangeScope);
  const [planBusy, setPlanBusy] = useState(false);

  // 切会话 / 刷新页面时回填计划状态（状态在磁盘上，会话恢复后仍在）
  useEffect(() => {
    if (!activeSessionId) {
      setPlan(null);
      return;
    }
    let cancelled = false;
    getPlan(activeSessionId)
      .then((p) => { if (!cancelled) setPlan(p); })
      .catch(() => { /* 计划端点不可用不影响对话 */ });
    getGoal(activeSessionId)
      .then((g) => { if (!cancelled) setGoal(g); })
      .catch(() => { /* 目标端点不可用不影响对话 */ });
    getPendingAsk(activeSessionId)
      .then((q) => { if (!cancelled) setPendingAsk(q); })
      .catch(() => { /* 提问端点不可用不影响对话 */ });
    return () => { cancelled = true; };
  }, [activeSessionId, setPlan, setGoal, setPendingAsk]);

  // 一轮跑完（streaming true→false）再拉变更卡：检查点由工具执行后的后台快照生成，
  // 流还没结束时拉会拿到上一轮的快照。切会话时清空，避免显示别的会话的变更。
  useEffect(() => {
    if (!activeSessionId) return;
    if (streaming) return;
    let cancelled = false;
    const pull = () => getChanges(activeSessionId, undefined, changeScope)
      .then((doc) => { if (!cancelled) setChanges(doc); })
      .catch(() => { /* 变更端点不可用不影响对话 */ });
    void pull();
    // 【E1-3 余项·卡片时序】检查点由工具执行后的**后台**快照生成（去抖 + 异步）：
    // 流刚结束时首拉可能还是上一轮快照 ⇒ 1.5s 后再拉一次（不假装首拉就新鲜）。
    const timer = setTimeout(pull, 1500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [activeSessionId, streaming, changeScope, setChanges]);

  const togglePlanMode = async () => {
    if (!activeSessionId || planBusy) return;
    setPlanBusy(true);
    try {
      setPlan(await setPlanMode(activeSessionId, !plan?.plan_mode));
    } catch {
      /* 令牌/网络失败时保持原状，用户可从计划卡错误区看到具体原因 */
    } finally {
      setPlanBusy(false);
    }
  };

  // 对话显示与风格偏好（思考/工具开关 + 输出格式 + 主题/气泡/字号）
  const showThinking = useChatPrefsStore((s) => s.showThinking);
  const showToolCalls = useChatPrefsStore((s) => s.showToolCalls);
  const toggleDisplay = useChatPrefsStore((s) => s.toggleDisplay);
  const theme = useChatPrefsStore((s) => s.theme);
  const bubbleStyle = useChatPrefsStore((s) => s.bubbleStyle);
  const fontSize = useChatPrefsStore((s) => s.fontSize);
  const transcriptView = useChatPrefsStore((s) => s.transcriptView);
  const collapseTiming = useChatPrefsStore((s) => s.collapseTiming);
  const foldedReplies = useChatPrefsStore((s) => s.foldedReplies);
  const styleVars = chatStyleVars({ theme, bubbleStyle, fontSize });

  // 轮次信息（每条消息属于哪一轮、这一轮是否已"翻篇"、是否轮内最后一条回复）：
  // 折叠时机「下次输入时折叠」与「仅最终回复」都依赖它，见 chat/transcriptFold.ts
  const turnMeta = useMemo(() => computeTurnMeta(messages), [messages]);

  // 本对话可显隐的步骤数（思考链 / 工具调用）——用于在开关上给出可感知的状态：
  // 计数为 0 时说明"当前没有可显隐的内容"，避免用户以为开关坏了。
  const stepCounts = useMemo(() => {
    let thoughts = 0;
    let tools = 0;
    for (const m of messages) {
      for (const s of m.steps ?? []) {
        if (isToolStep(s)) tools += 1;
        else thoughts += 1;
      }
    }
    return { thoughts, tools };
  }, [messages]);

  const [input, setInput] = useState('');
  //: 被引用的消息原文（空 = 无引用）；由 MessageItem 的「引用」按钮写入
  const [quoted, setQuoted] = useState('');
  //: 已上传附件（E3-3）；发送时拼成消息前缀，发完清空
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([]);
  // 斜杠命令注册表（已发布技能 → /skill:<id>，输入 / 时提示）
  interface SlashCmd { token?: string; id?: string; name?: string; description?: string; content_type?: string; category?: string; version?: string }
  const typeLabel = (c: SlashCmd): string => {
    const ct = String(c.content_type ?? '').toUpperCase();
    return ct.slice(0, 6) || 'SKILL';
  };
  const [cmds, setCmds] = useState<SlashCmd[]>([]);
  useEffect(() => {
    let cancelled = false;
    // 【2026-10-01 修：裸 fetch 不带令牌】enforce/enforce_all 档下该请求恒 401，
    //   斜杠命令提示因此静默失效（实测：有令牌时该端点仍 401）。
    //   统一走 lib/apiToken 的 authHeader()，与 apiClient 同一口径。
    fetch(SKILLS_MGMT_SLASH_COMMANDS, { headers: authHeader() })
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
        const brief = String(cmd.description ?? '').trim().slice(0, 140);
        // 展开为可执行调用指令：名称/id + 任务 + 技能用途说明（随消息进入会话上下文，模型按此执行）
        return `请调用已注册技能「${cmd.name ?? cmd.id}」（${cmd.id}）。${task ? `任务：${task}` : '请按该技能用途处理。'}${brief ? `\n技能用途：${brief}` : ''}`;
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

  // 【2026-10-15 修「刷新后会话从头刷到尾」】
  // 历史消息是 loadSessionHistory 一次性 set 进来的：若仍用 smooth，浏览器会带着整段
  // 会话从顶部平滑滚到末尾，长会话看起来就像"从头刷到尾"。历史批次直接落底（auto），
  // 只有本次会话新产生的消息才平滑滚动。
  const lastIsHistory = messages.length > 0 && isHistoryMessageId(messages[messages.length - 1].id);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: lastIsHistory ? 'auto' : 'smooth', block: 'end' });
  }, [messages, lastMsgContent, lastIsHistory]);

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
    // 【E3-3 · 2026-11-27】引用：把被引消息原文拼成 Markdown 引用块放在最前面。
    // 只在用户真的点了「引用」时加，且发完即清（否则下一句会莫名带着上一句的引用）。
    const prefix = quoted ? '> ' + quoted.replace(/\s*\n\s*/g, ' ') + '\n\n' : '';
    // 【E3-3】附件清单走在引用块**前面**：先声明"这次带了什么文件"，再说引用了哪句
    const manifest = buildAttachmentManifest(attachments);
    sendMessage(manifest + prefix + expandSlash(input));
    setInput('');
    setQuoted('');
    setAttachments([]);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 对话工具条：思考/工具显示开关（legacy 💭 Thought / 🔧 工具）+ 输出格式切换 + 风格设置 */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-800/70 bg-slate-900/30 px-4 py-1.5">
        <Button
          data-testid="display-toggle"
          onClick={() => toggleDisplay('thinking')}
          aria-pressed={showThinking}
          title={
            stepCounts.thoughts > 0
              ? `显示/隐藏对话内的思考过程（本对话 ${stepCounts.thoughts} 步）。右侧「思考过程」面板始终展示完整链路。`
              : '显示/隐藏对话内的思考过程（当前对话暂无可显隐的思考步骤）'
          }
          className={stepCounts.thoughts === 0 ? 'opacity-60' : undefined}
          size="xs"
          variant="default"
          active={showThinking}
        >
          💭 思考
          {stepCounts.thoughts > 0 && (
            <Badge tone="neutral" size="sm" data-testid="display-count">{stepCounts.thoughts}</Badge>
          )}
        </Button>
        <Button
          data-testid="display-toggle"
          onClick={() => toggleDisplay('toolcalls')}
          aria-pressed={showToolCalls}
          title={
            stepCounts.tools > 0
              ? `显示/隐藏对话内的工具调用步骤（本对话 ${stepCounts.tools} 次）。`
              : '显示/隐藏对话内的工具调用步骤（本次对话未触发工具调用，故暂无可显隐内容）'
          }
          className={stepCounts.tools === 0 ? 'opacity-60' : undefined}
          size="xs"
          variant="default"
          active={showToolCalls}
        >
          🔧 工具
          {stepCounts.tools > 0 && (
            <Badge tone="neutral" size="sm" data-testid="display-count">{stepCounts.tools}</Badge>
          )}
        </Button>

        {/* 对话模式（后端跑哪条链路：轻量 / 检索 / 完整）
            【2026-11-21】原先它左边是「输出格式」快捷切换器；该切换器已并入
            「外观」弹层（ChatStyleMenu）—— 输出格式与主题/气泡/字号同属"外观"，
            不再在工具栏单独占一个入口。 */}
        <ChatModeMenu />

        {/* 权限档位（工具要不要逐条审批）—— 紧邻对话模式：一个管"怎么跑"，一个管"授权多大" */}
        <PermissionModeMenu />

        {/* 计划模式（E1-1）：管"先规划还是直接做"；与权限档位正交，不改变审批边界 */}
        <Button
          data-testid="plan-mode-toggle"
          onClick={togglePlanMode}
          aria-pressed={!!plan?.plan_mode}
          disabled={!activeSessionId || planBusy}
          title={
            plan?.plan_mode
              ? '退出计划模式（计划文件与历史保留）'
              : '进入计划模式：只做只读调研并产出一份计划，批准后才执行'
          }
          size="xs"
          variant="default"
          active={!!plan?.plan_mode}
        >
          🧭 计划
          {plan?.plan_mode && plan.status === 'pending_approval' && (
            <Badge tone="warning" size="sm" data-testid="plan-pending-badge">待批</Badge>
          )}
        </Button>

        {/* 内联审批弹窗：SSE approval 事件 ⇒ 点头即批并自动续跑（不必去审批面板/手打继续） */}
        <ApprovalPrompt />

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-micro text-slate-600 sm:inline">风格设置可调主题 / 气泡 / 字号</span>
          <ChatStyleMenu />
        </div>
      </div>

      {/* 目标进度条（E1-2d）：无目标时 GoalBar 自身返回 null，不占位 */}
      {goal && <GoalBar goal={goal} />}

      {/* 消息区 */}
      <div
        ref={scrollRef}
        className="wb-chat-surface wb-chat-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4"
        style={styleVars as React.CSSProperties}
      >
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
                <Button
                  key={s}
                  type="button"
                  variant="default"
                  size="sm"
                  className="justify-start whitespace-normal text-left"
                  onClick={() => sendMessage(s)}
                  disabled={streaming}
                >
                  {s}
                </Button>
              ))}
            </div>
          </motion.div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            <AnimatePresence initial={false}>
              {messages.map((m, index) => {
                const meta = turnMeta[index];
                // 「折叠保留 = 仅最终回复」：折叠掉的中间回复整条不渲染（过程与正文一起收）
                const foldedProcess = m.role === 'assistant' && shouldFoldProcess({
                  mode: transcriptView,
                  timing: collapseTiming,
                  status: m.status,
                  isHistory: isHistoryMessageId(m.id),
                  turnClosed: meta?.turnClosed ?? false,
                  hasSteps: (m.steps ?? []).length > 0,
                });
                if (shouldHideIntermediateReply(foldedReplies, foldedProcess, meta)) return null;
                return (
                  <MessageItem
                    key={m.id}
                    message={m}
                    highlighted={m.id === highlightMsgId}
                    onQuote={setQuoted}
                    turnMeta={meta}
                  />
                );
              })}
            </AnimatePresence>
            <div ref={bottomRef} />
            {changes && <ChangeCard changes={changes} scope={changeScope} onScopeChange={setChangeScope} />}
            {presented && presented.files.length > 0 && (
              <PresentCard files={presented.files} note={presented.note} />
            )}
          </div>
        )}
      </div>

      {/* 计划卡（E1-1）：只在计划模式开启时占位；已批准后自动消失（plan_mode=false） */}
      {plan?.plan_mode && (
        <div className="shrink-0 border-t border-amber-900/30 bg-slate-950/40 px-4 pt-2">
          <PlanCard plan={plan} />
        </div>
      )}

      {/* 结构化提问（E1-3a）：有未答问题时占位；提交后由 store 清掉 */}
      {pendingAsk && (
        <div className="shrink-0 border-t border-cyan-900/30 bg-slate-950/40 px-4 pt-2">
          <QuestionCard ask={pendingAsk} />
        </div>
      )}

      {/* 输入区 */}
      <div className="wb-input-pad border-t border-slate-800/60 px-4 py-3" onKeyDownCapture={onSlashKey}>
        {streaming && (
          <div className="mb-2 flex items-center justify-between">
            <span className="flex items-center gap-2 text-xs text-cyan-400/80">
              <span className="wb-pulse-dot" />
              正在生成…
            </span>
            <Button type="button" variant="danger" size="sm" onClick={stopStreaming}>
              <Square size={11} fill="currentColor" />
              停止生成
            </Button>
          </div>
        )}
        {slashHits.length > 0 && (
          <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/95 p-1 shadow-xl">
            <div className="flex items-center justify-between px-2 pb-1 pt-0.5">
              <span className="text-micro uppercase tracking-wider text-slate-500">已注册技能斜杠（↑↓ 选择 · Enter 选中即发送 · Tab 仅插入）</span>
              {slashHits.length > 1 && <span className="font-mono text-micro text-cyan-400/70">{hiSafe + 1}/{slashHits.length}</span>}
            </div>
            {slashHits.map((c, idx) => (
              <Button variant="plain" size="bare"
                key={c.token}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); pickSlash(c, false); }}
                onMouseEnter={() => setHi(idx)}
                className={`flex w-full items-center justify-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-cyan-500/10 ${idx === hiSafe ? 'bg-cyan-500/15 ring-1 ring-cyan-600/50' : ''}`}
              >
                <span className="shrink-0 font-mono text-caption text-cyan-300">{c.token}</span>
                <span className="shrink-0 rounded-sm border border-cyan-800/50 bg-cyan-500/10 px-1 text-micro font-medium text-cyan-300" title={`类型 ${c.content_type ?? ''}`}>{typeLabel(c)}</span>
                <span className="min-w-0 flex-1 truncate text-caption text-slate-200">{c.name}</span>
                <span className="shrink-0 text-micro text-slate-600">{c.version ? `v${c.version}` : ''}</span>
                <span className="shrink-0 text-micro text-slate-600">{c.category ? `[${c.category}]` : ''}</span>
                <span className="hidden max-w-40 min-w-0 truncate text-micro text-slate-500 sm:inline">{c.description}</span>
              </Button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2">
          <AttachmentPicker
            sessionId={activeSessionId ?? undefined}
            value={attachments}
            onChange={setAttachments}
            disabled={streaming}
          />
          {/* E3-4：语音输入（录音 → 转写 → 回填输入框；未启用时只给提示） */}
          <VoiceInputButton
            disabled={streaming}
            onText={(text) => setInput((prev) => (prev ? prev + ' ' + text : text))}
          />
        </div>
        <MessageInput
          value={input}
          onChange={setInput}
          onSend={handleSend}
          disabled={streaming}
          quoted={quoted}
          onClearQuote={() => setQuoted('')}
        />
      </div>
    </div>
  );
}
