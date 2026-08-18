/**
 * 单条消息：Framer Motion 入场动画 + 流式光标
 */
import { motion } from 'framer-motion';
import type { ChatMessage } from '../../../stores/useLayoutStore';
import { Markdown } from './Markdown';

const formatTime = (ts: number) =>
  new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

export function MessageItem({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const streaming = message.status === 'streaming';

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, filter: 'blur(2px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      className={`wb-msg flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}
    >
      {/* 头像 */}
      <div
        className={`wb-msg-avatar shrink-0 ${
          isUser ? 'bg-sky-500/20 text-sky-300' : 'bg-cyan-400/15 text-cyan-300'
        }`}
      >
        {isUser ? '我' : '枢'}
      </div>

      <div className={`flex min-w-0 max-w-[82%] flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}>
        <span className="wb-msg-time font-mono text-[11px] text-slate-500">
          {formatTime(message.createdAt)}
        </span>

        <div
          className={`wb-bubble ${
            isUser
              ? 'rounded-2xl rounded-tr-md border-sky-400/20 bg-sky-500/10 text-sky-50'
              : 'rounded-2xl rounded-tl-md border-cyan-400/15 bg-slate-900/50 text-slate-100'
          }`}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words text-[13.5px] leading-relaxed">
              {message.content}
            </p>
          ) : (
            <>
              <Markdown content={message.content} />
              {streaming && <span className="wb-caret" aria-hidden />}
            </>
          )}
        </div>
      </div>
    </motion.div>
  );
}
