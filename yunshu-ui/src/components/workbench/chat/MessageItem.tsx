/**
 * 单条消息：Framer Motion 入场动画 + 流式光标
 *
 * data-mid：以消息 ID 作为 DOM 定位锚点，供"历史问话跳转定位"精确滚动/高亮
 * （配合 ChatPanel 的 highlightMsgId 消费逻辑与 useLayoutStore.setHighlightMsg）。
 *
 * 显示能力（对应「恢复工具调用过程和思考过程的显示」+「多种对话输出格式」）：
 *  - 思考过程（💭）与工具调用步骤（🔧）在**对话内联**渲染，由 useChatPrefsStore
 *    的显示开关控制显隐；
 *  - 渲染条件基于**是否存在步骤**，而不是"步骤里有没有文本"：后端 `done` 事件本身
 *    不带 detail，若按文本判空，回复一完成思考块就会整块消失（线上实测缺陷）；
 *  - 关闭开关时给出「已隐藏 N 步」的可点击提示条 —— 让开关的效果可见可逆；
 *  - 输出格式（气泡 / 紧凑 / 终端）通过 .wb-chat-format-* 类切换整体观感，
 *    颜色主题 / 气泡圆角 / 字号由容器上的 --chat-* CSS 变量驱动（见 useChatPrefsStore）。
 */
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui';
import { motion } from 'framer-motion';
import { CheckCircle2, ChevronDown, ChevronRight, EyeOff, Loader2, Quote, Wrench, XCircle } from 'lucide-react';
import { isHistoryMessageId, useLayoutStore, type ChatMessage, type ThinkingEvent } from '../../../stores/useLayoutStore';
import { useChatPrefsStore } from '../../../stores/useChatPrefsStore';
import { Markdown } from './Markdown';
import { MessageFeedback } from './MessageFeedback';
import { findQuotedMessage, parseLeadingQuote } from './quoteAnchor';
import { foldPreview, shouldFoldProcess, type TurnMeta } from './transcriptFold';

const formatTime = (ts: number) =>
  new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

/**
 * 工具调用步骤的判定：后端 SSE 的真实工具调用事件 title 固定为「工具调用：<name>」。
 * 注意必须带冒号 —— 推理链路里的阶段事件「工具调用」（无具体工具名）不算工具步骤，
 * 否则会被误渲染成一个空工具块。
 *
 * 导出供 ChatPanel 统计「本对话可显隐的步骤数」，让两个显示开关的状态可见。
 */
export const isToolStep = (s: ThinkingEvent) => (s.title ?? '').startsWith('工具调用：');

function StepStatusIcon({ status }: { status: ThinkingEvent['status'] }) {
  if (status === 'running') return <Loader2 size={11} className="animate-spin text-cyan-300" />;
  if (status === 'done') return <CheckCircle2 size={11} className="text-emerald-400" />;
  if (status === 'error') return <XCircle size={11} className="text-rose-400" />;
  return <Wrench size={11} className="text-slate-500" />;
}

/** 折叠块：点击标题展开/收起（工具调用与思考过程共用） */
function CollapsibleBlock({
  className,
  icon,
  title,
  meta,
  defaultOpen = false,
  children,
}: {
  className: string;
  icon: React.ReactNode;
  title: string;
  meta?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`${className} ${open ? 'open' : ''}`}>
      <Button type="button" variant="plain" size="bare" className="wb-step-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {icon}
        <span className="wb-step-title">{title}</span>
        {meta && <span className="wb-step-meta">{meta}</span>}
      </Button>
      {open && <div className="wb-step-body">{children}</div>}
    </div>
  );
}

/** 单步条目（思考链路逐条列出：阶段事件 / 推理内容 / 工具调用） */
function StepRow({ step }: { step: ThinkingEvent }) {
  return (
    <div className={`wb-step-row ${step.status}`}>
      <span className="wb-step-row-head">
        <StepStatusIcon status={step.status} />
        <span className="wb-step-row-title">{step.title.replace(/^工具调用：/, '')}</span>
        <span className="wb-step-row-status">
          {step.status === 'running' ? '进行中' : step.status === 'done' ? '完成' : step.status}
        </span>
      </span>
      {step.detail ? <pre className="wb-step-text">{step.detail}</pre> : null}
    </div>
  );
}

export function MessageItem({
  message,
  highlighted = false,
  onQuote,
  turnMeta,
}: {
  message: ChatMessage;
  /** 历史问话跳转目标：短暂高亮气泡 */
  highlighted?: boolean;
  /** 【E3-3】把这条消息引用到输入框（未传 = 不提供引用入口，如只读视图） */
  onQuote?: (text: string) => void;
  /** 轮次位置（ChatPanel 一次算好传下来；缺省按"未翻篇"处理 ⇒ 保守不折叠） */
  turnMeta?: TurnMeta;
}) {
  const isUser = message.role === 'user';
  const streaming = message.status === 'streaming';
  const showThinking = useChatPrefsStore((s) => s.showThinking);
  const showToolCalls = useChatPrefsStore((s) => s.showToolCalls);
  const setDisplay = useChatPrefsStore((s) => s.setDisplay);
  const format = useChatPrefsStore((s) => s.format);
  // 工作细节（四档 + 折叠时机）：见 transcriptFold.ts 的判定矩阵
  const transcriptView = useChatPrefsStore((s) => s.transcriptView);
  const collapseTiming = useChatPrefsStore((s) => s.collapseTiming);
  const [processOpenOverride, setProcessOpenOverride] = useState<boolean | null>(null);
  // 【E3-3】逐条反馈要带会话 id（反馈中心的按会话检索用），与消息同源于 layout store
  const sessionId = useLayoutStore((s) => s.activeSessionId ?? undefined);
  // 【E3-3 尾巴】引用块可点击跳回原消息（纯前端：消息里已有 DOM 锚点 data-mid）
  const quote = isUser ? parseLeadingQuote(message.content) : null;
  const jumpToQuoted = () => {
    if (!quote) return;
    const all = useLayoutStore.getState().messages;
    const target = findQuotedMessage(all, quote.quoted);
    if (!target) return;
    useLayoutStore.getState().setHighlightMsg(target.id);
    try {
      const el = document.querySelector('[data-mid="' + target.id + '"]');
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch {
      /* 滚动失败不影响高亮 */
    }
  };

  const steps = message.steps ?? [];
  const thoughtSteps = steps.filter((s) => !isToolStep(s));
  const toolSteps = steps.filter(isToolStep);
  const runningThoughts = thoughtSteps.filter((s) => s.status === 'running').length;
  const runningTools = toolSteps.filter((s) => s.status === 'running').length;

  const folded = !isUser && shouldFoldProcess({
    mode: transcriptView,
    timing: collapseTiming,
    status: message.status,
    isHistory: isHistoryMessageId(message.id),
    turnClosed: turnMeta?.turnClosed ?? false,
    hasSteps: steps.length > 0,
  });
  // 两个总闸都关掉时不折叠（继续走原来的"已隐藏 N 步"提示，别让折叠行挡住恢复入口）
  const foldedView = folded && (showThinking || showToolCalls);
  // 折叠判定的结果变化时（例如"下次输入时折叠"这一刻到来）清掉手动展开，回到策略默认
  useEffect(() => {
    setProcessOpenOverride(null);
  }, [foldedView]);
  const processOpen = foldedView ? (processOpenOverride ?? false) : true;
  const preview = foldPreview(transcriptView, steps);

  return (
    <motion.div
      data-mid={message.id}
      // 历史消息（刷新/切会话时一次载入）不播放入场动画：否则长会话会逐条淡入，
      // 与"从顶部平滑滚到底"叠在一起，观感就是"从头刷到尾"（见 useLayoutStore 的
      // HISTORY_MSG_ID_PREFIX 说明）。initial={false} 即关闭该元素的首次动画。
      initial={isHistoryMessageId(message.id) ? false : { opacity: 0, y: 12, filter: 'blur(2px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      className={`wb-msg wb-chat-format-${format} flex gap-3 ${isUser ? 'flex-row-reverse' : ''} ${highlighted ? 'wb-msg-flash' : ''}`}
    >
      {/* 头像（紧凑/终端格式下由 CSS 隐藏） */}
      <div
        className={`wb-msg-avatar shrink-0 ${
          isUser ? 'bg-sky-500/20 text-sky-300' : 'bg-cyan-400/15 text-cyan-300'
        }`}
      >
        {isUser ? '我' : '枢'}
      </div>

      <div className={`wb-msg-col flex min-w-0 max-w-[82%] flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}>
        <span className="wb-msg-time font-mono text-caption text-slate-500">
          {format === 'compact' && <em className="wb-msg-role">{isUser ? '我' : '云枢'}</em>}
          {formatTime(message.createdAt)}
        </span>

        {/* 工作细节 · 折叠态：完成的轮次把「思考 + 工具」收成一行（档位/时机见 transcriptFold.ts） */}
        {foldedView && !processOpen && (
          <Button
            type="button"
            variant="plain"
            size="bare"
            className="wb-fold-chip"
            aria-expanded={false}
            title="展开这一轮的过程"
            onClick={() => setProcessOpenOverride(true)}
          >
            <ChevronRight size={11} />
            <span>过程 · {steps.length} 步</span>
            {preview ? <span className="wb-fold-preview">{preview}</span> : null}
          </Button>
        )}

        {/* 思考过程：有步骤就渲染（与文本是否为空无关），逐条列出阶段/推理。
            默认展开 —— 否则回复完成后只剩一行标题，看起来仍像"消失"；用户可手动折叠。 */}
        {(!foldedView || processOpen) && !isUser && showThinking && thoughtSteps.length > 0 && (
          <CollapsibleBlock
            className="wb-thought-block"
            icon={<span className="wb-step-emoji">💭</span>}
            title="思考过程"
            meta={runningThoughts > 0 ? `${runningThoughts} 步进行中` : `Thought · ${thoughtSteps.length} 步`}
            defaultOpen
          >
            <div className="wb-step-list">
              {thoughtSteps.map((s, i) => (
                <StepRow key={`${s.id}-${i}`} step={s} />
              ))}
            </div>
          </CollapsibleBlock>
        )}

        {/* 工具调用步骤：每次调用一块，含状态 + 参数/结果 */}
        {(!foldedView || processOpen) && !isUser && showToolCalls && toolSteps.length > 0 && (
          <div className="wb-tool-steps">
            <div className="wb-tool-steps-head">
              🔧 工具调用 <em>{toolSteps.length}</em>
              {runningTools > 0 && <span className="wb-tool-running">{runningTools} 个执行中</span>}
            </div>
            {toolSteps.map((s, i) => (
              <CollapsibleBlock
                key={`${s.id}-${i}`}
                className="wb-tool-step"
                icon={<StepStatusIcon status={s.status} />}
                title={s.title.replace(/^工具调用：/, '')}
                meta={s.status === 'running' ? '执行中' : s.status === 'done' ? '完成' : s.status}
                defaultOpen
              >
                <pre className="wb-step-text">{s.detail ?? '（无详情）'}</pre>
              </CollapsibleBlock>
            ))}
          </div>
        )}

        {/* 折叠态下手动展开后的「收起」入口 */}
        {foldedView && processOpen && (
          <Button
            type="button"
            variant="plain"
            size="bare"
            className="wb-fold-chip"
            aria-expanded
            title="收起这一轮的过程"
            onClick={() => setProcessOpenOverride(false)}
          >
            <ChevronDown size={11} />
            收起过程
          </Button>
        )}

        {/* 开关关闭时的提示条：让「显示/隐藏」开关的效果可见、可一键恢复 */}
        {(!foldedView || processOpen) && !isUser && !showThinking && thoughtSteps.length > 0 && (
          <Button
            type="button"
            variant="plain"
            size="bare"
            className="wb-hidden-chip"
            onClick={() => setDisplay('thinking', true)}
            title="点击重新显示思考过程"
          >
            <EyeOff size={10} /> 思考过程已隐藏（{thoughtSteps.length} 步）· 点击显示
          </Button>
        )}
        {(!foldedView || processOpen) && !isUser && !showToolCalls && toolSteps.length > 0 && (
          <Button
            type="button"
            variant="plain"
            size="bare"
            className="wb-hidden-chip"
            onClick={() => setDisplay('toolcalls', true)}
            title="点击重新显示工具调用步骤"
          >
            <EyeOff size={10} /> 工具调用已隐藏（{toolSteps.length} 步）· 点击显示
          </Button>
        )}

        {/* 字号跟随「外观 → 字体大小」（--chat-fs 挂在 .wb-chat-surface 上）：
            这里不要写死 text-body-lg，否则改字号对真实消息无效（2026-11-21 修）。 */}
        {quote && (
          <Button
            type="button"
            variant="plain"
            size="bare"
            className="wb-quoted-ref"
            title="跳到被引用的消息"
            aria-label="跳到被引用的消息"
            onClick={jumpToQuoted}
          >
            <Quote size={10} />
            <span className="min-w-0 truncate">{quote.quoted.slice(0, 60)}</span>
          </Button>
        )}
        <div className={`wb-bubble ${isUser ? 'wb-bubble-user' : 'wb-bubble-bot'}`}>
          {isUser ? (
            <p className="whitespace-pre-wrap wrap-break-word leading-relaxed">
              {quote ? quote.rest || quote.quoted : message.content}
            </p>
          ) : (
            <>
              <Markdown content={message.content} />
              {streaming && <span className="wb-caret" aria-hidden />}
            </>
          )}
        </div>

        {/* 【E3-3 · 2026-11-27】消息动作条：引用（两侧都可）+ 逐条反馈（只 assistant）。
            反馈只对已完成消息显示 —— 流式中点踩会把半句话记成不满意。 */}
        <div className="wb-msg-actions">
          {onQuote && !streaming && (
            <Button
              type="button"
              variant="plain"
              size="bare"
              className="wb-vote wb-quote-btn"
              title="引用这条消息"
              aria-label="引用这条消息"
              onClick={() => onQuote(message.content)}
            >
              <Quote size={12} />
            </Button>
          )}
          {!isUser && !streaming && (
            <MessageFeedback messageId={message.id} sessionId={sessionId} />
          )}
        </div>
      </div>
    </motion.div>
  );
}
