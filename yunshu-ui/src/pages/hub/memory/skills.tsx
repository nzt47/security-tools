/**
 * 技能库管理 —— 技能启停 / 参数配置
 * 数据源：/api/skills、/api/skills/toggle、/api/skills/params
 */
import { useEffect, useState } from 'react'
import { Power } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost , pickList } from '../components/ui'

interface Skill {
  id: string
  name: string
  enabled: boolean
  description?: string
  params?: Record<string, unknown>
}

export default function MemorySkills() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    hubGet('/api/skills').then((r) => {
      const installed = pickList<Skill>(r, 'installed')
      const available = pickList<Skill>(r, 'available')
      setSkills(installed.length > 0 ? installed : available)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const toggle = async (id: string) => {
    try {
      await hubPost('/api/skills/toggle', { id })
      load()
    } catch (e) { setError(String(e)) }
  }

  return (
    <div className="p-6">
      <PageHeader title="技能库管理" description="技能的启用、停用与参数配置" />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={skills}
            keyField="id"
            columns={[
              { key: 'name', title: '技能', render: (r) => (
                <div>
                  <div className="font-medium text-slate-200">{r.name}</div>
                  {r.description && <div className="text-xs text-slate-500">{r.description}</div>}
                </div>
              ) },
              { key: 'enabled', title: '状态', render: (r) => (
                <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge>
              ) },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <button
                    onClick={() => toggle(r.id)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs ${
                      r.enabled ? 'bg-slate-800 text-slate-300 hover:bg-slate-700' : 'bg-emerald-600 text-white hover:bg-emerald-500'
                    }`}
                  >
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
