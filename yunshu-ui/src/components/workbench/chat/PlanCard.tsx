/**
 * PlanCard —— 计划模式计划卡（E1-1）
 * ------------------------------------------------
 * 位置：会话页消息区与输入区之间（不新开页面，深链不变）。
 * 数据：store.plan（GET /api/plan-mode/state 回填 + SSE plan 事件 + 本卡操作响应）。
 *
 * 交互语义（与设计页 §3 状态机一致）：
 *   批准      → 计划 approved、退出计划模式；随后把 next_message 作为普通用户消息发出（开始执行）。
 *   反馈并继续 → 记录意见、留在计划模式；随后发送意见 → 新回复成为新版本。
 *   拒绝并继续 → 标记该版本 rejected、留在计划模式；随后发送拒绝说明 → 继续规划。
 *   回滚 vN    → 把历史版本提升为新版本并重新待批（不删除历史）。
 * 服务端不自己发起模型轮：三种"继续"都复用普通对话链路（记忆/审计/落盘同一口径）。
 */
import { useState } from 'react';
import { GitBranch, Loader2, ShieldCheck, Undo2, X } from 'lucide-react';
import { Badge, Button, Card, Textarea } from '@/components/ui';
import { Markdown } from './Markdown';
import { decidePlan, setPlanMode, type PlanAction } from '@/lib/planApi';
import type { PlanState } from '@/lib/sse';
import { useLayoutStore } from '@/stores/useLayoutStore';

/** 状态 → 人读标签/色调（颜色只用于徽标，不改变卡片结构） */
const STATUS_LABEL: Record<string, { text: string; tone: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' }> = {
  idle: { text: '空闲', tone: 'neutral' },
  planning: { text: '规划中', tone: 'primary' },
  pending_approval: { text: '待批准', tone: 'warning' },
  approved: { text: '已批准', tone: 'success' },
  rejected: { text: '已拒绝', tone: 'danger' },
};

/** 最近展示的版本数（历史仍全部保留，回滚面板只列最近几版） */
const VISIBLE_VERSIONS = 5;

export function PlanCard({ plan }: { plan: PlanState }) {
  const setPlan = useLayoutStore((s) => s.setPlan);
  const sendMessage = useLayoutStore((s) => s.sendMessage);
  const streaming = useLayoutStore((s) => s.streaming);

  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState<PlanAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 计划全文折叠（长计划默认展开，可收起腾出对话空间） */
  const [collapsed, setCollapsed] = useState(false);

  const status = STATUS_LABEL[plan.status] ?? STATUS_LABEL.idle;
  const pending = plan.status === 'pending_approval';
  const disabled = busy !== null || streaming;

  const run = async (action: PlanAction, opts: { feedback?: string; version?: number } = {}) => {
    if (disabled) return;
    setBusy(action);
    setError(null);
    try {
      const res = await decidePlan(plan.session_id, action, opts);
      setPlan(res);
      if (res.next_message) {
        await sendMessage(res.next_message);
      }
      if (action === 'feedback' || action === 'reject') setFeedback('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const exitPlanMode = async () => {
    if (disabled) return;
    setBusy('reject');
    setError(null);
    try {
      setPlan(await setPlanMode(plan.session_id, false));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const versions = [...plan.revisions].slice(-VISIBLE_VERSIONS).reverse();

  return (
    <Card
      data-testid="plan-card"
      className="mx-auto mb-2 w-full max-w-3xl border-amber-700/40 bg-amber-500/5 p-3"
    >
      <div className="flex items-center gap-2">
        <ShieldCheck size={15} className="shrink-0 text-amber-400" />
        <h3 className="text-ui font-medium text-amber-200">计划模式</h3>
        <Badge tone={status.tone} size="sm" data-testid="plan-status">{status.text}</Badge>
        <span className="text-micro text-slate-500">v{plan.version}</span>
        {plan.session_id && (
          <span className="ml-auto flex items-center gap-1">
            <Button
              type="button"
              size="xs"
              variant="ghost"
              onClick={exitPlanMode}
              disabled={disabled}
              title="退出计划模式（计划文件与历史保留）"
            >
              <X size={11} /> 退出
            </Button>
          </span>
        )}
      </div>

      <div className={collapsed ? 'mt-2 hidden' : 'mt-2 max-h-56 overflow-y-auto rounded-md border border-slate-800/60 bg-slate-950/40 px-3 py-2'}>
        {plan.plan ? (
          <Markdown content={plan.plan} />
        ) : (
          <p className="text-caption text-slate-500">
            还没有计划草稿。在计划模式下发送调研目标，助手的第一条回复会成为计划草稿。
          </p>
        )}
      </div>

      {plan.plan && (
        <Button
          type="button"
          variant="plain"
          size="bare"
          onClick={() => setCollapsed((v) => !v)}
          className="mt-1 text-micro text-slate-500 hover:text-slate-300"
        >
          {collapsed ? '展开计划全文' : '收起计划全文'}
        </Button>
      )}

      {/* 反馈输入：只在待批准/规划中显示（已批准后卡片会自动收起） */}
      {plan.plan_mode && (
        <Textarea
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="给计划的反馈（可选；批准不需要填）"
          rows={2}
          size="sm"
          className="mt-2"
          data-testid="plan-feedback"
          disabled={disabled}
        />
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="primary"
          data-testid="plan-approve"
          disabled={!pending || disabled}
          onClick={() => run('approve')}
          title={pending ? '批准并开始执行' : '当前没有待批准的计划'}
        >
          {busy === 'approve' ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} />}
          批准并执行
        </Button>
        <Button
          type="button"
          size="sm"
          variant="default"
          data-testid="plan-feedback-submit"
          disabled={!feedback.trim() || disabled}
          onClick={() => run('feedback', { feedback: feedback.trim() })}
          title="把意见作为用户消息发出，助手据此产出下一版计划"
        >
          {busy === 'feedback' ? <Loader2 size={12} className="animate-spin" /> : <GitBranch size={12} />}
          反馈并继续规划
        </Button>
        <Button
          type="button"
          size="sm"
          variant="danger-outline"
          data-testid="plan-reject"
          disabled={disabled}
          onClick={() => run('reject', feedback.trim() ? { feedback: feedback.trim() } : {})}
          title="拒绝本版计划（仍留在计划模式，继续规划）"
        >
          拒绝
        </Button>
        {versions.length > 1 && (
          <span className="ml-auto flex items-center gap-1 text-micro text-slate-500">
            <Undo2 size={11} /> 回滚：
            {versions.map((rev) => (
              <Button
                key={rev.version}
                type="button"
                size="xs"
                variant="plain"
                data-testid={`plan-rollback-${rev.version}`}
                disabled={disabled}
                onClick={() => run('rollback', { version: rev.version })}
                title={`回滚到 v${rev.version}（${rev.at}）`}
                className="rounded border border-slate-700/70 px-1 font-mono text-micro text-slate-400 hover:text-slate-200"
              >
                v{rev.version}
              </Button>
            ))}
          </span>
        )}
      </div>

      {error && (
        <p className="mt-1 text-micro text-rose-400" data-testid="plan-error">{error}</p>
      )}
    </Card>
  );
}

export default PlanCard;
