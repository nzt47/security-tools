/**
 * 工作流详情弹窗 —— 让「工作流技能」Tab 能看清这条工作流是做什么的、里面有哪几步。
 *
 * 为什么单独抽出来：LLM 技能有「查看内容」（skill.md 正文），工作流此前只有 name/匹配数/启停，
 * 用户看不到 description、触发词与步骤定义。这里把 /api/workflow-learning/workflows 已经返回的
 * 完整 model_dump（description / trigger_patterns / steps / 统计）如实呈现，不新增后端接口。
 */
import { X } from 'lucide-react'

export interface WorkflowStep {
  step_id?: string
  tool_name?: string
  params_template?: Record<string, unknown> | null
  condition?: string | null
  need_llm?: boolean
  prompt_template?: string
  timeout_ms?: number
  output_key?: string
  description?: string
}

export interface WorkflowDetail {
  id: string
  name?: string
  description?: string
  workflow_type?: string
  status?: string
  enabled?: boolean
  steps?: WorkflowStep[]
  trigger_patterns?: string[]
  confidence?: number
  success_count?: number
  failure_count?: number
  observed_count?: number
  priority?: number
  source_user_input?: string
  source_session_id?: string
  tags?: string[]
  created_at?: string
  updated_at?: string
  last_used_at?: string | null
  converted_to_skill_id?: string
}

function fmtTime(v?: string | null): string {
  if (!v) return '—'
  const s = String(v)
  return s.length >= 19 ? s.slice(0, 19).replace('T', ' ') : s
}

function typeLabel(t?: string): string {
  return t === 'hybrid' ? '工作流（可含 LLM 步骤）' : '工作流技能（纯工具链 · 免 LLM）'
}

export function WorkflowDetailModal({ wf, onClose }: { wf: WorkflowDetail; onClose: () => void }) {
  const steps = wf.steps ?? []
  const triggers = wf.trigger_patterns ?? []
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="工作流详情"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-slate-100">{wf.name || wf.id}</h2>
            <p className="mt-0.5 font-mono text-[11px] text-slate-500">{wf.id}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭" className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        <p className="mt-3 whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
          {wf.description || wf.source_user_input || '（该工作流没有填写说明）'}
        </p>

        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] text-slate-400 sm:grid-cols-3">
          <div><dt className="text-slate-500">类型</dt><dd className="text-slate-300">{typeLabel(wf.workflow_type)}</dd></div>
          <div><dt className="text-slate-500">状态</dt><dd className="text-slate-300">{wf.enabled ? '启用' : '停用'} · {wf.status ?? '—'}</dd></div>
          <div><dt className="text-slate-500">置信度</dt><dd className="font-mono text-slate-300">{typeof wf.confidence === 'number' ? wf.confidence.toFixed(2) : '—'}</dd></div>
          <div><dt className="text-slate-500">执行</dt><dd className="text-slate-300">成功 {wf.success_count ?? 0} / 失败 {wf.failure_count ?? 0}</dd></div>
          <div><dt className="text-slate-500">被学习</dt><dd className="text-slate-300">{wf.observed_count ?? 1} 次</dd></div>
          <div><dt className="text-slate-500">优先级</dt><dd className="text-slate-300">{wf.priority ?? '—'}</dd></div>
          <div><dt className="text-slate-500">创建</dt><dd className="text-slate-300">{fmtTime(wf.created_at)}</dd></div>
          <div><dt className="text-slate-500">更新</dt><dd className="text-slate-300">{fmtTime(wf.updated_at)}</dd></div>
          <div><dt className="text-slate-500">最后使用</dt><dd className="text-slate-300">{fmtTime(wf.last_used_at)}</dd></div>
        </dl>

        {triggers.length > 0 && (
          <div className="mt-3">
            <div className="text-[11px] text-slate-500">触发模式（命中即本地执行）</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {triggers.slice(0, 24).map((t, i) => (
                <span key={`${t}-${i}`} className="rounded border border-slate-700 bg-slate-950 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">{t}</span>
              ))}
              {triggers.length > 24 && <span className="text-[10px] text-slate-500">…共 {triggers.length} 个</span>}
            </div>
          </div>
        )}

        <div className="mt-4">
          <div className="mb-1 text-[11px] text-slate-500">步骤（{steps.length}）</div>
          {steps.length === 0 ? (
            <p className="text-xs text-slate-500">该工作流没有定义步骤。</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-800">
              <table className="w-full text-left text-[11px]">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-500">
                    <th className="px-2 py-1.5 font-medium">#</th>
                    <th className="px-2 py-1.5 font-medium">工具</th>
                    <th className="px-2 py-1.5 font-medium">参数模板</th>
                    <th className="px-2 py-1.5 font-medium">条件</th>
                    <th className="px-2 py-1.5 font-medium">执行方</th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((s, i) => (
                    <tr key={s.step_id || i} className="border-b border-slate-800/50 align-top">
                      <td className="px-2 py-1.5 font-mono text-slate-500">{s.step_id || i + 1}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-200">{s.tool_name || (s.need_llm ? '(LLM)' : '—')}</td>
                      <td className="px-2 py-1.5 text-slate-400">
                        <span className="block max-w-[260px] truncate font-mono" title={JSON.stringify(s.params_template ?? {})}>
                          {JSON.stringify(s.params_template ?? {})}
                        </span>
                        {s.need_llm && s.prompt_template ? <span className="mt-0.5 block max-w-[260px] truncate text-slate-500" title={s.prompt_template}>{s.prompt_template}</span> : null}
                      </td>
                      <td className="px-2 py-1.5 font-mono text-slate-400">{s.condition || '—'}</td>
                      <td className="px-2 py-1.5 text-slate-400">{s.need_llm ? 'LLM' : '本地工具'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="mt-3 grid gap-1 text-[11px] text-slate-500">
          {wf.source_user_input && <p className="truncate" title={wf.source_user_input}>来源任务：{wf.source_user_input}</p>}
          {wf.source_session_id && <p className="font-mono">来源会话：{wf.source_session_id}</p>}
          {wf.converted_to_skill_id && <p className="font-mono">已导出 LLM 参考：{wf.converted_to_skill_id}</p>}
          {(wf.tags ?? []).length > 0 && <p>标签：{(wf.tags ?? []).join('、')}</p>}
        </div>
      </div>
    </div>
  )
}
