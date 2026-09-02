/**
 * MCP 系统 —— MCP 服务 CRUD / 启停
 * 数据源：/api/mcp/services、/api/mcp/enable
 */
import { useEffect, useState } from 'react'
import { Plug, Plus, Trash2, Power } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, unwrap , pickList } from '../components/ui'

interface McpService {
  service_id: string
  id?: string
  name?: string
  command?: string
  transport?: string
  enabled?: boolean
  status?: string
  [k: string]: unknown
}

export default function ToolsMcp() {
  const [services, setServices] = useState<McpService[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [cmd, setCmd] = useState('')

  const load = () => {
    setLoading(true)
    hubGet('/api/mcp/services').then((r) => {
      setServices(pickList<McpService>(r, 'services'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const add = async () => {
    try {
      await hubPost('/api/mcp/services', { name, command: cmd })
      setShowForm(false)
      setName('')
      setCmd('')
      load()
    } catch (e) { setError(String(e)) }
  }

  const del = async (id: string) => {
    try {
      await hubPost(`/api/mcp/services/${id}/delete`, {})
      load()
    } catch (e) { setError(String(e)) }
  }

  const toggle = async (id: string) => {
    try {
      await hubPost('/api/mcp/enable', { service_id: id })
      load()
    } catch (e) { setError(String(e)) }
  }

  const sid = (s: McpService) => String(s.service_id ?? s.id ?? '')

  return (
    <div className="p-6">
      <PageHeader
        title="MCP 系统"
        description="MCP 服务管理与启停"
        actions={<button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"><Plus size={12} /> 新增服务</button>}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {showForm && (
        <Card className="mb-4">
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="服务名称" className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="启动命令 (command)" className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <button onClick={add} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500">保存</button>
          </div>
        </Card>
      )}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={services}
            keyField="service_id"
            columns={[
              { key: 'name', title: '服务', render: (r) => <span className="text-slate-200">{String(r.name ?? sid(r))}</span> },
              { key: 'transport', title: '传输', render: (r) => (r.transport ? <Badge color="cyan">{String(r.transport)}</Badge> : '-') },
              { key: 'enabled', title: '状态', render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <div className="flex gap-2">
                    <button onClick={() => toggle(sid(r))} className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
                      <Power size={11} /> 切换
                    </button>
                    <button onClick={() => del(sid(r))} className="flex items-center gap-1 rounded-md border border-red-900/60 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-950">
                      <Trash2 size={11} /> 删除
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
