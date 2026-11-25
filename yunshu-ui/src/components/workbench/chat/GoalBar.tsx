/**
 * GoalBar —— 目标进度条（E1-2d）
 * ------------------------------------------------
 * 位置：会话页工具栏下方、消息区上方（一行；不新增页面/叶子 ⇒ 深链不变）。
 * 数据：store.goal（GET /api/goal/state 回填 + SSE goal 事件 + 本组件操作响应）。
 *
 * 交互：暂停 / 继续 / 清除（用户侧操作，不进 LLM）；展开可看验收条件与最近事件，
 * 并可追加验收条件。目标为空（idle）时整条不渲染。
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, Target, Trash2 } from 'lucide-react';
import { Badge, Button, Input } from '@/components/ui';
import { acceptGoal, setGoalMode } from '@/lib/goalApi';
import type { GoalState } from '@/lib/sse';
import { useLayoutStore } from '@/stores/useLayoutStore';

const STATE_META: Record<string, { text: string; tone: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' }> = {
  active: { text: '进行中', tone: 'primary' },
  paused: { text: '已暂停', tone: 'warning' },
  blocked: { text: '阻塞', tone: 'danger' },
  completed: { text: '已完成', tone: 'success' },
  idle: { text: '空闲', tone: 'neutral' },
};

export function GoalBar({ goal }: { goal: GoalState }) {
  const setGoal = useLayoutStore((s) => s.setGoal);
  const streaming = useLayoutStore((s) => s.streaming);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [item, setItem] = useState('');
  const [error, setError] = useState<string | null>(null);

  if (!goal.goal_id || goal.state === 'idle') return null;

  const meta = STATE_META[goal.state] ?? STATE_META.idle;
  const sess = goal.session_id;

  const run = async (fn: () => Promise<GoalState>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setGoal(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const blocked = goal.blocked;

  return (
    <div
      data-testid="goal-bar"
      className="shrink-0 border-b border-cyan-900/40 bg-cyan-500/5 px-4 py-1.5"
    >
      <div className="mx-auto flex max-w-3xl items-center gap-2">
        <Target size={14} className="shrink-0 text-cyan-400" />
        <span className="truncate text-ui font-medium text-cyan-200" title={goal.title}>
          {goal.title}
        </span>
        <Badge tone={meta.tone} size="sm" data-testid="goal-state">{meta.text}</Badge>
        <span className="shrink-0 text-micro text-slate-500" data-testid="goal-round">
          轮次 {goal.round}/{goal.max_rounds}
        </span>
        {blocked && (
          <span className="truncate text-micro text-rose-400" data-testid="goal-blocked">
            {blocked.code}：{blocked.reason}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {goal.state === 'active' || goal.state === 'blocked' ? (
            <Button
              type="button" size="xs" variant="default"
              data-testid="goal-pause"
              disabled={busy || streaming}
              onClick={() => run(() => setGoalMode(sess, 'pause', '用户暂停'))}
              title="暂停目标（driver 不再自动续跑）"
            >
              暂停
            </Button>
          ) : (
            <Button
              type="button" size="xs" variant="default"
              data-testid="goal-resume"
              disabled={busy || streaming || goal.state === 'completed'}
              onClick={() => run(() => setGoalMode(sess, 'resume'))}
              title={goal.state === 'completed' ? '已完成的目标不能恢复' : '恢复目标'}
            >
              继续
            </Button>
          )}
          <Button
            type="button" size="xs" variant="ghost"
            data-testid="goal-clear"
            disabled={busy}
            onClick={() => run(() => setGoalMode(sess, 'clear'))}
            title="清除目标（事件流保留，可回放）"
          >
            <Trash2 size={11} /> 清除
          </Button>
          <Button
            type="button" size="xs" variant="ghost"
            data-testid="goal-expand"
            onClick={() => setOpen((v) => !v)}
            title={open ? '收起详情' : '展开验收条件与最近事件'}
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} 详情
          </Button>
          {busy && <Loader2 size={12} className="animate-spin text-slate-500" />}
        </span>
      </div>

      {open && (
        <div className="mx-auto mt-1.5 max-w-3xl rounded-md border border-slate-800/60 bg-slate-950/40 px-3 py-2">
          {goal.why && <p className="text-caption text-slate-400">为什么：{goal.why}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <span className="text-micro text-slate-500">验收条件：</span>
            {goal.acceptance.length === 0 ? (
              <span className="text-micro text-slate-600">（未设置 —— 建议补一条可验收标准）</span>
            ) : (
              goal.acceptance.map((a) => (
                <Badge key={a} tone="neutral" size="sm">{a}</Badge>
              ))
            )}
          </div>
          {goal.state !== 'completed' && (
            <div className="mt-2 flex items-center gap-2">
              <Input
                value={item}
                onChange={(e) => setItem(e.target.value)}
                placeholder="追加一条验收条件"
                size="sm"
                data-testid="goal-accept-input"
                disabled={busy}
              />
              <Button
                type="button" size="xs" variant="default"
                data-testid="goal-accept-submit"
                disabled={busy || !item.trim()}
                onClick={() => run(async () => {
                  const st = await acceptGoal(sess, item.trim());
                  setItem('');
                  return st;
                })}
              >
                添加
              </Button>
            </div>
          )}
          {goal.recent_events && goal.recent_events.length > 0 && (
            <p className="mt-2 truncate text-micro text-slate-600">
              最近事件：{goal.recent_events.slice(-5).map((e) => e.type).join(' → ')}
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="mx-auto mt-1 max-w-3xl text-micro text-rose-400" data-testid="goal-error">
          {error}
        </p>
      )}
    </div>
  );
}

export default GoalBar;
