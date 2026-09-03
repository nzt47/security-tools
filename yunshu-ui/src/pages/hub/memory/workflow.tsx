/**
 * 工作流技能管理 —— 学习到的工作流列表与启停（本地确定性执行）
 * 数据源：/api/workflow-learning/workflows
 *
 * 说明：工作流技能 = 从历史交互学习/可视化编排的确定性工具步骤；
 * 命中后本地直接执行（skipped_llm），不消耗 LLM。与「LLM 技能」分开管理，
 * 见技能中心（LLM 技能 / 工作流技能 / 可视化编辑三个 Tab）。
 */
import { useEffect, useState } from 'react'
import { Play } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, pickList } from '../components/ui'

interface Workflow {
  id: string
  name?: string
  pattern?: string
  enabled?: boolean
  match_count?: number
  [k: string]: unknown
}

/** 列表体（技能中心「工作流技能」Tab 复用；页面/中心各自提供外壳） */
export function MemoryWorkflowTable() {
  const [items, setItems] = useState<Workflow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [execMsg, setExecMsg] = useState('')

  const load = () => {
    setLoading(true)
    hubGet('/api/workflow-learning/workflows').then((r) => {
      setItems(pickList<Workflow>(r, 'items'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const toggle = async (id: string) => {
    try {
      await hubPost(`/api/workflow-learning/workflows/${id}/toggle`)
      load()
    } catch (e) { setError(String(e)) }
  }

  const exec = async (id: string) => {
    try {
      const r = await hubPost(`/api/workflow-learning/execute/${id}`)
      setExecMsg(`执行结果：${JSON.stringify(r).slice(0, 120)}`)
    } catch (e) { setExecMsg(`执行失败：${e instanceof Error ? e.message : e}`) }
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
            columns={[
              { key: 'name', title: '工作流', render: (r) => <span className="text-slate-200">{String(r.name ?? r.pattern ?? r.id)}</span> },
              { key: 'match_count', title: '匹配数', render: (r) => <span className="font-mono">{String(r.match_count ?? '-')}</span> },
              { key: 'enabled', title: '状态', render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <div className="flex gap-2">
                    <button onClick={() => exec(r.id)} className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500">
                      <Play size={11} /> 执行
                    </button>
                    <button onClick={() => toggle(r.id)} className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
                      {r.enabled ? '停用' : '启用'}
                    </button>
                  </div>
                ),
              },
            ]}
          />
        </Card>
      )}
    </div>
  )
}

export default function MemoryWorkflow() {
  return (
    <div className="p-6">
      <PageHeader title="工作流技能" description="从历史交互中学习、本地确定性执行的工作流" />
      <MemoryWorkflowTable />
    </div>
  )
}
