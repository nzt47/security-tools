/**
 * 单条消息的点赞 / 点踩（E3-3「逐条反馈」）
 *
 * 【为什么复用 /api/feedback/submit 而不新建路由】该端点本来就收 like/dislike + context，
 * 而 L5 语义质量探针只数 like/dislike（口径 §12）⇒ 逐条投票直接成为满意度信号，
 * 不需要第二套存储、也不给路由覆盖率添债务。trace_id 用消息 id。
 *
 * 【为什么用裸 fetch 而不是 postEnvelope】该端点尚未迁到 v2 信封（返回 {success, data}），
 * 信封解析会在成功响应上抛错。与 lib/alarms.ts 同一种写法：只认 res.ok，失败回滚并给一行提示。
 *
 * 【本地记忆】投票结果落 localStorage（按消息 id）：刷新/切会话后按钮仍是已选态，
 * 避免同一句话被反复计票（后端没有去重键，去重放在最靠近用户的这一层）。
 */
import { useState } from 'react';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import { Button } from '@/components/ui';
import { FEEDBACK_SUBMIT } from '@/api/endpoints';
import { authHeader } from '../../../lib/apiToken';

type Vote = 'like' | 'dislike';

const STORAGE_PREFIX = 'yunshu.msg.feedback.';

function readStored(messageId: string): Vote | null {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + messageId);
    return raw === 'like' || raw === 'dislike' ? raw : null;
  } catch {
    return null;                      // 隐私模式/配额满 ⇒ 当没投过，不阻塞交互
  }
}

function writeStored(messageId: string, vote: Vote | null) {
  try {
    if (vote) localStorage.setItem(STORAGE_PREFIX + messageId, vote);
    else localStorage.removeItem(STORAGE_PREFIX + messageId);
  } catch {
    /* 存不下不影响本次提交 */
  }
}

const voteClass = (active: boolean) => (active ? 'wb-vote is-on' : 'wb-vote');

export function MessageFeedback({
  messageId,
  sessionId,
}: {
  messageId: string;
  sessionId?: string;
}) {
  const [vote, setVote] = useState<Vote | null>(() => readStored(messageId));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  async function submit(next: Vote) {
    if (pending) return;
    const previous = vote;
    const target: Vote | null = previous === next ? null : next;   // 再点一次 = 取消
    setPending(true);
    setError('');
    if (!target) {
      // 取消不改后端（没有撤回接口），只清本地标记：如实，不假装撤销了服务端记录
      setVote(null);
      writeStored(messageId, null);
      setPending(false);
      return;
    }
    setVote(target);
    try {
      const res = await fetch(FEEDBACK_SUBMIT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({
          trace_id: messageId,
          feedback_type: target,
          session_id: sessionId ?? '',
          context: {
            source: 'chat_message',
            message_id: messageId,
            role: 'assistant',
          },
        }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      writeStored(messageId, target);
    } catch {
      setVote(previous);              // 失败回滚（不留下「已投」的假象）
      setError('提交失败');
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="wb-msg-feedback">
      <Button
        type="button"
        variant="plain"
        size="bare"
        className={voteClass(vote === 'like')}
        title={vote === 'like' ? '已点赞（再点取消）' : '这条回复有帮助'}
        aria-label="这条回复有帮助"
        aria-pressed={vote === 'like'}
        disabled={pending}
        onClick={() => void submit('like')}
      >
        <ThumbsUp size={12} />
      </Button>
      <Button
        type="button"
        variant="plain"
        size="bare"
        className={voteClass(vote === 'dislike')}
        title={vote === 'dislike' ? '已点踩（再点取消）' : '这条回复不对'}
        aria-label="这条回复不对"
        aria-pressed={vote === 'dislike'}
        disabled={pending}
        onClick={() => void submit('dislike')}
      >
        <ThumbsDown size={12} />
      </Button>
      {error ? <span className="text-micro text-rose-400">{error}</span> : null}
    </div>
  );
}
