/**
 * 资产管理 —— 8 类资产集中管理与备份
 * 数据源：/api/assets/overview、/api/assets/<category>、/api/assets/backup、/api/assets/backup/list
 * 类别：memory/prompts/tools/skills/habits/inspires/hobbies/interactions
 */
import { useEffect, useState } from 'react'
import { Database, Archive, Plus, Trash2 } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, unwrap , pickList, pickObj } from '../components/ui'

const CATEGORIES = [
  { key: 'memory', label: '记忆数据', icon: '🧠' },
  { key: 'prompts', label: '提示词库', icon: '📝' },
  { key: 'tools', label: '工具资源', icon: '🛠' },
  { key: 'skills', label: '技能与工作流', icon: '🔧' },
  { key: 'habits', label: '用户习惯', icon: '📌' },
  { key: 'inspires', label: '灵感想法', icon: '💡' },
  { key: 'hobbies', label: '爱好创造', icon: '🎨' },
  { key: 'interactions', label: '交互记忆', icon: '💬' },
]

interface AssetItem {
  id: string
  title?: string
  name?: string
  description?: string
  created_at?: string
  [k: string]: unknown
}

interface BackupItem {
  id?: string
  backup_id?: string
  created_at?: string
  size?: number
  [k: string]: unknown
}

export default function AssetsPage() {
  const [overview, setOverview] = useState<Record<string, number>>({})
  const [active, setActive] = useState('memory')
  const [items, setItems] = useState<AssetItem[]>([])
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [newTitle, setNewTitle] = useState('')

  const loadOverview = () => {
    hubGet('/api/assets/overview').then((r) => {
      const d = pickObj<Record<string, unknown>>(r) ?? {}
      const ov = ((d['overview'] as Record<string, number> | undefined) ?? d) as Record<string, number>
      setOverview(ov)
    }).catch(() => {})
  }

  const loadList = (cat: string) => {
    setLoading(true)
    hubGet(`/api/assets/${cat}`).then((r) => {
      setItems(pickList<AssetItem>(r, 'items'))
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  const loadBackups = () => {
    hubGet('/api/assets/backup/list').then((r) => {
      setBackups(pickList<BackupItem>(r, 'backups'))
    }).catch(() => {})
  }

  useEffect(() => {
    loadOverview()
    loadList(active)
    loadBackups()
  }, [])

  const switchCat = (cat: string) => {
    setActive(cat)
    loadList(cat)
  }

  const addItem = async () => {
    if (!newTitle.trim()) return
    try {
      await hubPost(`/api/assets/${active}`, { title: newTitle.trim() })
      setNewTitle('')
      loadList(active)
      loadOverview()
    } catch (e) { setError(String(e)) }
  }

  const delItem = async (id: string) => {
    try {
      await hubPost(`/api/assets/${active}/${encodeURIComponent(id)}/delete`, {})
      loadList(active)
      loadOverview()
    } catch (e) { setError(String(e)) }
  }

  const backup = async () => {
    try {
      const r = await hubPost('/api/assets/backup')
      setMsg(`备份完成：${JSON.stringify(r).slice(0, 100)}`)
      loadBackups()
    } catch (e) { setError(String(e)) }
  }

  const activeCat = CATEGORIES.find((c) => c.key === active)

  return (
    <div className="p-6">
      <PageHeader
        title="资产管理"
        description="集中式管理与备份系统 —— 8 类资产"
        actions={
          <button onClick={backup} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs text-white hover:bg-blue-500">
            <Archive size={12} /> 创建备份
          </button>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{msg}</div>}

      {/* 类别概览 */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {CATEGORIES.map((c) => (
          <button
            key={c.key}
            onClick={() => switchCat(c.key)}
            className={`flex items-center justify-between rounded-xl border p-3 text-left transition-colors ${
              active === c.key ? 'border-cyan-700 bg-cyan-950/40' : 'border-slate-800 bg-slate-900/60 hover:bg-slate-800/50'
            }`}
          >
            <div>
              <div className="text-lg">{c.icon}</div>
              <div className="mt-1 text-xs text-slate-400">{c.label}</div>
            </div>
            <span className="text-xl font-semibold text-white">{overview[c.key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card title={`${activeCat?.icon ?? ''} ${activeCat?.label ?? active}（${items.length}）`}>
            {loading ? <Loading /> : (
              <>
                <DataTable
                  data={items}
                  keyField="id"
                  columns={[
                    { key: 'title', title: '条目', render: (r) => (
                      <div>
                        <div className="text-slate-200">{String(r.title ?? r.name ?? r.id)}</div>
                        {r.description && <div className="max-w-md truncate text-xs text-slate-500">{String(r.description)}</div>}
                      </div>
                    ) },
                    { key: 'created_at', title: '创建时间', render: (r) => <span className="font-mono text-xs text-slate-500">{String(r.created_at ?? '')}</span> },
                    {
                      key: 'actions', title: '',
                      render: (r) => (
                        <button onClick={() => delItem(String(r.id))} className="flex items-center gap-1 rounded-md border border-red-900/60 px-2 py-1 text-xs text-red-400 hover:bg-red-950">
                          <Trash2 size={11} /> 删除
                        </button>
                      ),
                    },
                  ]}
                />
                <div className="mt-3 flex gap-2">
                  <input
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && addItem()}
                    placeholder={`添加${activeCat?.label ?? active}条目…`}
                    className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
                  />
                  <button onClick={addItem} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500">
                    <Plus size={14} /> 添加
                  </button>
                </div>
              </>
            )}
          </Card>
        </div>

        <Card title="备份记录">
          {backups.length === 0 ? (
            <div className="py-8 text-center text-sm text-slate-600">暂无备份</div>
          ) : (
            <div className="space-y-2">
              {backups.map((b) => (
                <div key={String(b.id ?? b.backup_id ?? '')} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                  <div>
                    <div className="font-mono text-xs text-slate-300">{String(b.backup_id ?? b.id)}</div>
                    <div className="text-xs text-slate-500">{String(b.created_at ?? '')}</div>
                  </div>
                  {b.size != null && <Badge color="cyan">{Math.round(Number(b.size) / 1024)} KB</Badge>}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
