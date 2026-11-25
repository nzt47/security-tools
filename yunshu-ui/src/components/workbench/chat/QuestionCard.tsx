/**
 * QuestionCard —— 结构化提问卡（E1-3a）
 * ------------------------------------------------
 * 位置：会话页输入区上方（醒目、不遮消息）。数据：store.pendingAsk
 * （SSE `ask` 事件 / GET /api/ask/pending 恢复）。
 *
 * 交互语义：选择（单选/多选）+ 可选自定义 → 提交 → 服务端记台账并返回 next_message
 * → 本条卡片收起 + 用同一条 sendMessage 续跑（与 plan/goal 同一模式）。
 */
import { useMemo, useState } from 'react';
import { HelpCircle, Loader2, Send } from 'lucide-react';
import { Badge, Button, Card, Checkbox, Radio, Textarea } from '@/components/ui';
import { answerAsk } from '@/lib/askApi';
import type { AskState } from '@/lib/sse';
import { useLayoutStore } from '@/stores/useLayoutStore';

export function QuestionCard({ ask }: { ask: AskState }) {
  const sessionId = useLayoutStore((s) => s.activeSessionId);
  const setPendingAsk = useLayoutStore((s) => s.setPendingAsk);
  const sendMessage = useLayoutStore((s) => s.sendMessage);
  const streaming = useLayoutStore((s) => s.streaming);

  const [picked, setPicked] = useState<string[]>([]);
  const [custom, setCustom] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const options = useMemo(() => ask.options ?? [], [ask.options]);
  const multi = !!ask.allow_multiple;
  const canSubmit = !!sessionId && (picked.length > 0 || custom.trim().length > 0) && !busy;

  const toggle = (id: string) => {
    setPicked((prev) => {
      if (multi) return prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      return prev.includes(id) ? [] : [id];
    });
  };

  const submit = async () => {
    if (!sessionId || !canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const res = await answerAsk(sessionId, ask.question_id, picked, custom.trim());
      setPendingAsk(null);
      if (res.next_message) void sendMessage(res.next_message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      data-testid="question-card"
      className="mx-auto mb-2 w-full max-w-3xl border-cyan-700/40 bg-cyan-500/5 p-3"
    >
      <div className="flex items-start gap-2">
        <HelpCircle size={15} className="mt-0.5 shrink-0 text-cyan-400" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-ui font-medium text-cyan-200">需要你确认</h3>
            {multi && <Badge tone="neutral" size="sm">可多选</Badge>}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-caption text-slate-200" data-testid="question-text">
            {ask.question}
          </p>
          {ask.context && <p className="mt-1 text-micro text-slate-500">{ask.context}</p>}
        </div>
      </div>

      {options.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5" data-testid="question-options">
          {options.map((opt) => {
            const checked = picked.includes(opt.id);
            const onChange = () => toggle(opt.id);
            return (
              <label
                key={opt.id}
                className={`flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-1.5 ${
                  checked
                    ? 'border-cyan-600/60 bg-cyan-500/10'
                    : 'border-slate-800/70 hover:border-slate-700'
                }`}
              >
                {multi ? (
                  <Checkbox size="sm" checked={checked} onChange={onChange}
                    aria-label={opt.label} />
                ) : (
                  <Radio size="sm" name={`ask-${ask.question_id}`} checked={checked}
                    onChange={onChange} aria-label={opt.label} />
                )}
                <span className="min-w-0">
                  <span className="block text-caption text-slate-200">{opt.label}</span>
                  {opt.description && (
                    <span className="block text-micro text-slate-500">{opt.description}</span>
                  )}
                </span>
              </label>
            );
          })}
        </div>
      )}

      {ask.allow_custom && (
        <Textarea
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          placeholder={options.length ? '其他（可选，自己填写）' : '输入你的回答'}
          rows={2}
          size="sm"
          className="mt-2"
          data-testid="question-custom"
          disabled={busy}
        />
      )}

      <div className="mt-2 flex items-center gap-2">
        <Button
          type="button" size="sm" variant="primary" data-testid="question-submit"
          disabled={!canSubmit} onClick={submit}
          title={canSubmit ? '提交回答并让模型继续' : '请先选择或填写回答'}
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
          提交回答
        </Button>
        <span className="text-micro text-slate-500">
          回答会作为一条用户消息进入对话（与手打同一条链路）
        </span>
      </div>

      {error && (
        <p className="mt-1 text-micro text-rose-400" data-testid="question-error">{error}</p>
      )}
    </Card>
  );
}

export default QuestionCard;
