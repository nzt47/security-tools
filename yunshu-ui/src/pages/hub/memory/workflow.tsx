/**
 * 工作流技能管理 —— 学习到的工作流列表与启停（本地确定性执行）
 * 数据源：/api/workflow-learning/workflows（返回完整 model_dump）
 *
 * 2026-10-23：与「LLM 技能」对齐信息密度 —— 列表补「说明 / 步骤链 / 类型 / 执行统计」，
 * 并新增「查看」弹窗（workflow-detail.tsx）把 description、触发词、步骤定义完整展示出来。
 * 此前这张表只有 名称 / 匹配数（该字段后端并不存在，恒为 -）/ 状态 / 操作。
 */
import { useEffect, useState } from 'react'
import { Eye, Play } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, hubPost } from '../components/ui'

import {
  WORKFLOW_LEARNING_WORKFLOWS,
  workflowLearnConvertToSkill,
  workflowLearnExecute,
  workflowLearnToggle,
} from '@/api/endpoints';
// 【P1-front 第二批】后端该端点已带 X-Envelope: v2，故改用显式信封解析，
// 不再用 pickList 在候选键里"找数组"（那种写法换包法时会静默返回空列表）。
import { getEnvelope } from '@/api/envelope';
import { WorkflowDetailModal, type WorkflowDetail, type WorkflowStep } from './workflow-detail'

interface Workflow extends WorkflowDetail {
  match_count?: number
  [k: string]: unknown
}

/** `/api/workflow-learning/workflows` 的业务载荷（后端：`{ok, items, total}`）。 */
interface WorkflowList {
  ok?: boolean
  items?: Workflow[]
  total?: number
}

/** 一句话说明：优先 description，其次来源任务，最后用步骤链兜底（不留空列）。 */
export function workflowSummary(wf: Workflow): string {
  if (wf.description && wf.description.trim()) return wf.description.trim()
  if (wf.source_user_input && wf.source_user_input.trim()) return wf.source_user_input.trim()
  const tools = (wf.steps ?? []).map((s) => s.tool_name || (s.need_llm ? 'LLM' : '')).filter(Boolean)
  return tools.length ? `工具链：${tools.slice(0, 4).join(' → ')}` : '（无说明）'
}

/** 步骤链（工具名 → 工具名），用于列表里的「步骤」列。 */
export function workflowChain(steps?: WorkflowStep[]): string {
  const tools = (steps ?? []).map((s) => s.tool_name || (s.need_llm ? 'LLM' : '')).filter(Boolean)
  if (!tools.length) return '—'
  return tools.slice(0, 5).join(' → ') + (tools.length > 5 ? ' → …' : '')
}

/** 列表体（技能中心「工作流技能」Tab 复用；页面/中心各自提供外壳） */
export function MemoryWorkflowTable() {
  const [items, setItems] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [execMsg, setExecMsg] = useState('')
  const [detail, setDetail] = useState<Workflow | null>(null)

  const load = () => {
    setLoading(true)
    getEnvelope<WorkflowList>(WORKFLOW_LEARNING_WORKFLOWS).then((d) => {
      setItems(d?.items ?? [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const toggle = async (id: string) => {
    try {
      await hubPost(workflowLearnToggle(id))
      load()
    } catch (e) { setError(String(e)) }
  }

  const exec = async (id: string, fallbackTask?: string) => {
    try {
      const r = await hubPost(workflowLearnExecute(id), {
        task_text: fallbackTask || '执行该工作流',
      })
      setExecMsg(`执行结果：${JSON.stringify(r).slice(0, 120)}`)
    } catch (e) { setExecMsg(`执行失败：${e instanceof Error ? e.message : e}`) }
  }

  /** 工作流 → LLM 参考说明导出：把工作流步骤叙述成给 LLM 看的说明（markdown）。
      注意：此导出**不替代工作流本体**——原工作流仍在「工作流技能」Tab（本地执行
      免 LLM）；导出副本仅作 LLM 参考（skills-mgmt），不改变工作流的执行属性。
      满足质量门控(成功次数/置信度/ACTIVE)才可导出。 */
  const convert = async (wf: Workflow) => {
    try {
      const r = await hubPost<{ skill_id?: string; skill_name?: string; action?: string; error?: string; review?: { verdict?: string; status?: string }; digest?: { verdict?: string; status?: string } }>(
        workflowLearnConvertToSkill(wf.id),
        // 术语纪律（TASK-S0-01）：新参数 auto_review（评审语义；旧 auto_digest 由后端兼容）
        { force: false, auto_review: true },
      )
      if (r?.error) { setExecMsg(`导出失败：${r.error}`); return }
      const action = r?.action === 'already_converted' ? '已导出过' : '已导出为 LLM 参考'
      const review = r?.review ?? r?.digest  // 兼容旧服务端响应键 digest（≤1 minor）
      const verdictMsg = review?.verdict === 'block' ? '，评审存在阻断项(待人工复核)' : '，评审-评估通过'
      setExecMsg(`${action}「${r?.skill_name ?? wf.name ?? wf.id}」（skill_id=${r?.skill_id}）${verdictMsg}——本工作流仍保留在「工作流技能」Tab，本地执行免 LLM`)
      load()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setExecMsg(`导出失败：${msg}（未达质量门控时需先积累成功次数/置信度）`)
    }
  }

  return (
    <div>
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {execMsg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{execMsg}</div>}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={items}
            keyField="id"
            empty="暂无工作流。工作流由历史交互自动学习，或在「可视化编辑」里编排。"
            columns={[
              {
                key: 'name', title: '工作流',
                render: (r) => (
                  <div className="min-w-0">
                    <div className="truncate text-slate-200" title={String(r.name ?? r.id)}>{String(r.name ?? r.id)}</div>
                    <div className="font-mono text-[10px] text-slate-500">{r.id}</div>
                  </div>
                ),
              },
              {
                key: 'description', title: '说明',
                render: (r) => (
                  <div className="max-w-[360px] truncate text-slate-400" title={workflowSummary(r)}>{workflowSummary(r)}</div>
                ),
              },
              {
                key: 'steps', title: '步骤',
                render: (r) => (
                  <div className="min-w-0">
                    <div className="text-slate-300">{(r.steps ?? []).length} 步</div>
                    <div className="max-w-[220px] truncate font-mono text-[10px] text-slate-500" title={workflowChain(r.steps)}>{workflowChain(r.steps)}</div>
                  </div>
                ),
              },
              {
                key: 'workflow_type', title: '类型',
                render: (r) => (
                  <Badge color={r.workflow_type === 'hybrid' ? 'amber' : 'cyan'}>
                    {r.workflow_type === 'hybrid' ? '含 LLM 步骤' : '免 LLM 工具链'}
                  </Badge>
                ),
              },
              {
                key: 'enabled', title: '状态',
                render: (r) => (
                  <div className="flex flex-col items-start gap-1">
                    <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge>
                    {r.status && r.status !== 'active' ? <span className="font-mono text-[10px] text-slate-500">{r.status}</span> : null}
                  </div>
                ),
              },
              {
                key: 'stats', title: '执行',
                render: (r) => (
                  <div className="whitespace-nowrap font-mono text-[11px] text-slate-400">
                    成功 {r.success_count ?? 0} · 失败 {r.failure_count ?? 0}
                    {typeof r.confidence === 'number' ? <div className="text-[10px] text-slate-500">置信 {r.confidence.toFixed(2)}</div> : null}
                  </div>
                ),
              },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => setDetail(r)} className="flex items-center gap-1 rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800" title="查看说明、触发词与步骤定义">
                      <Eye size={11} /> 查看
                    </button>
                    <button onClick={() => exec(r.id, String(r.source_user_input ?? r.name ?? r.id ?? ''))} className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500" title="立即执行该工作流">
                      <Play size={11} /> 执行
                    </button>
                    <button onClick={() => toggle(r.id)} className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800" title={r.enabled ? '停用该工作流' : '启用该工作流'}>
                      {r.enabled ? '停用' : '启用'}
                    </button>
                    <button
                      onClick={() => convert(r)}
                      className="flex items-center gap-1 rounded-md border border-emerald-800/70 px-3 py-1.5 text-xs text-emerald-300 hover:bg-emerald-950/40"
                      title={
                        r.converted_to_skill_id
                          ? `已导出 LLM 参考 ${r.converted_to_skill_id}（再次点击返回既有参考）`
                          : '把工作流步骤导出为 LLM 参考说明（markdown，skills-mgmt）——不替代本工作流，本工作流仍本地执行免 LLM'
                      }
                    >
                      导出为 LLM 参考
                    </button>
                  </div>
                ),
              },
            ]}
          />
        </Card>
      )}
      {detail && <WorkflowDetailModal wf={detail} onClose={() => setDetail(null)} />}
    </div>
  )
}
