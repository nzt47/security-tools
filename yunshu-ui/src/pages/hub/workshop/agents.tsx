/**
 * 装配车间 —— 分身创建与组装（多 agent 设计系统）
 * 数据源：/api/subagent/list、/api/subagent/create、/api/subagent/<name>/destroy
 */
import { useEffect, useState } from 'react'
import { Users, Plus, Trash2, Rocket } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, unwrap , pickList } from '../components/ui'

interface Subagent {
  name: string
  model_id?: string
  memory_provider?: string
  status?: string
  tool_sources?: string[]
  tags?: string[]
  [k: string]: unknown
}

export default function WorkshopAgents() {
  const [agents, setAgents] = useState<Subagent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [model, setModel] = useState('gpt-4o')
  const [memory, setMemory] = useState('default')
  const [tools, setTools] = useState('')

  const load = () => {
    setLoading(true)
    hubGet('/api/subagent/list').then((r) => {
      setAgents(pickList<Subagent>(r, 'subagents'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const create = async () => {
    try {
      await hubPost('/api/subagent/create', {
        name,
        model_id: model,
        memory_provider: memory,
        tool_sources: tools ? tools.split(',').map((t) => t.trim()).filter(Boolean) : [],
        tags: ['hub'],
      })
      setShowForm(false)
      setName(''); setTools('')
      load()
    } catch (e) { setError(String(e)) }
  }

  const destroy = async (n: string) => {
    try {
      await hubPost(`/api/subagent/${encodeURIComponent(n)}/destroy`)
      load()
    } catch (e) { setError(String(e)) }
  }

  return (
    <div className="p-6">
      <PageHeader
        title="分身创建与组装"
        description="多 agent 设计系统 —— 分身生命周期管理"
        actions={<button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"><Plus size={12} /> 创建分身</button>}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {showForm && (
        <Card className="mb-4">
          <div className="grid gap-3 md:grid-cols-4">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="分身名称 *" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="模型 ID" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="记忆提供商" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={tools} onChange={(e) => setTools(e.target.value)} placeholder="工具源(逗号分隔)" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
          </div>
          <div className="mt-3 flex items-center gap-2">
            <button onClick={create} className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500">
              <Rocket size={14} /> 组装分身
            </button>
            <span className="text-xs text-slate-600">需管理员 token（FLASK_API_TOKEN）</span>
          </div>
        </Card>
      )}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={agents}
            keyField="name"
            columns={[
              { key: 'name', title: '分身', render: (r) => <span className="font-medium text-slate-200">{String(r.name)}</span> },
              { key: 'model_id', title: '模型', render: (r) => <span className="font-mono text-xs text-cyan-400">{String(r.model_id ?? '-')}</span> },
              { key: 'memory_provider', title: '记忆', render: (r) => <Badge color="cyan">{String(r.memory_provider ?? 'default')}</Badge> },
              { key: 'status', title: '状态', render: (r) => <Badge color={String(r.status) === 'running' ? 'green' : 'slate'}>{String(r.status ?? '?')}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <button onClick={() => destroy(String(r.name))} className="flex items-center gap-1 rounded-md border border-red-900/60 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-950">
                    <Trash2 size={11} /> 销毁
                  </button>
                ),
              },
            ]}
          />
        </Card>
      )}
    </div>
  )
}
