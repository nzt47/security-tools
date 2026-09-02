/**
 * ThinkingPanel —— 右侧"智能体思考过程 / 工具调用状态"面板
 * 事件由 store 的模拟流按序推入：意图识别 → 知识检索 → 规划分解 → 工具调用
 */
import { motion } from 'framer-motion';
import { Brain, CheckCircle2, Cog, Loader2, XCircle } from 'lucide-react';
import { useLayoutStore } from '../../../stores/useLayoutStore';

function StatusIcon({ status }: { status: 'pending' | 'running' | 'done' | 'error' }) {
  switch (status) {
    case 'running':
      return <Loader2 size={13} className="animate-spin text-cyan-300" />;
    case 'done':
      return <CheckCircle2 size={13} className="text-emerald-400" />;
    case 'error':
      return <XCircle size={13} className="text-rose-400" />;
    default:
      return <Cog size={13} className="text-slate-500" />;
  }
}

export function ThinkingPanel() {
  const thinking = useLayoutStore((s) => s.thinking);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-4 pb-2 pt-3 text-[11px] uppercase tracking-[0.18em] text-slate-500">
        <Brain size={12} />
        推理链路
      </div>

      <div className="wb-think-scroll min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        {thinking.length === 0 ? (
          <div className="mt-10 flex flex-col items-center gap-2 text-center">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/60 text-slate-600">
              <Cog size={16} />
            </div>
            <p className="text-xs text-slate-500">发起对话后，这里将实时呈现智能体的思考与工具调用。</p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {thinking.map((evt, i) => (
              <motion.div
                key={evt.id}
                initial={{ opacity: 0, x: 14 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.04 * i, duration: 0.22 }}
                className={`wb-think-node ${
                  evt.status === 'done'
                    ? 'border-emerald-400/15 bg-emerald-400/[0.04]'
                    : evt.status === 'running'
                      ? 'border-cyan-400/25 bg-cyan-400/[0.05]'
                      : 'border-slate-800 bg-slate-900/40'
                }`}
              >
                <div className="flex items-center gap-2">
                  <StatusIcon status={evt.status} />
                  <span className="text-[12.5px] font-medium text-slate-200">{evt.title}</span>
                  {evt.status === 'running' && (
                    <span className="ml-auto font-mono text-[10px] text-cyan-400/70">进行中</span>
                  )}
                  {evt.status === 'done' && (
                    <span className="ml-auto font-mono text-[10px] text-emerald-400/60">完成</span>
                  )}
                </div>
                {evt.detail && evt.status === 'running' && (
                  <p className="mt-1.5 pl-[21px] text-[11.5px] leading-relaxed text-slate-400">
                    {evt.detail}
                  </p>
                )}
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
