/**
 * 工具集 —— 工具配置 / 启停 / 分类
 * 数据源：/api/tools/config、/api/tools/toggle、/api/tools/categories
 */
import { useEffect, useState } from 'react'
import { Boxes, Power } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, unwrap , pickList } from '../components/ui'

interface ToolItem {
  name: string
  description?: string
  enabled?: boolean
  category?: string
  [k: string]: unknown
}

export default function ToolsToolset() {
  const [tools, setTools] = useState<ToolItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    hubGet('/api/tools/config').then((r) => {
      setTools(pickList<ToolItem>(r, 'tools'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const toggle = async (name: string) => {
    try {
      await hubPost('/api/tools/toggle', { name })
      load()
    } catch (e) { setError(String(e)) }
  }

  return (
    <div className="p-6">
      <PageHeader title="工具集" description="工具启停与分类配置" />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={tools}
            keyField="name"
            columns={[
              { key: 'name', title: '工具', render: (r) => (
                <div>
                  <div className="font-medium text-slate-200">{String(r.name)}</div>
                  {r.description && <div className="max-w-md text-xs text-slate-500">{String(r.description)}</div>}
                </div>
              ) },
              { key: 'category', title: '分类', render: (r) => (r.category ? <Badge color="cyan">{String(r.category)}</Badge> : '-') },
              { key: 'enabled', title: '状态', render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <button onClick={() => toggle(String(r.name))} className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs ${r.enabled ? 'bg-slate-800 text-slate-300 hover:bg-slate-700' : 'bg-emerald-600 text-white hover:bg-emerald-500'}`}>
                    <Power size={12} /> {r.enabled ? '停用' : '启用'}
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
