/**
 * 资产管理 —— 8 类资产集中管理与备份
 * 数据源：/api/assets/overview、/api/assets/<category>、/api/assets/backup、/api/assets/backup/list
 * 类别：memory/prompts/tools/skills/habits/inspires/hobbies/interactions
 *
 * 参数化（缺陷 ②）：同一组件被工作台 assets 组 8 个导航项复用，
 * 由导航 key（assets/<category>）推导的 initialCategory 决定初始分类，
 * 点击不同菜单即可渲染对应分类内容（配合 ContentPanel 的 key 重挂载）。
 */
import { useEffect, useState } from 'react'
import { Archive, Plus, Trash2 } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, hubDelete } from '../components/ui'
import {
  ASSETS_OVERVIEW,
  ASSETS_BACKUP,
  ASSETS_BACKUP_LIST,
  assetsByCategory,
  assetById,
} from '@/api/endpoints';

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

/** `/api/assets/overview` 的业务载荷（后端：`{ok, overview}`）。
 *
 * 【为什么写成类型而不是继续用 pickObj 猜】`pickObj(r) ?? (r as ...)` 会把
 * "取不到"变成静默的 `{}`，页面显示 0 而没有任何错误。显式取值则一眼可见。
 */
interface AssetsOverview {
  ok?: boolean
  overview?: Record<string, number>
}

/** `GET /api/assets/<category>` 的业务载荷（后端：`{ok, items, count, category}`）。 */
interface AssetsList {
  ok?: boolean
  items?: AssetItem[]
  count?: number
  category?: string
}

/** `GET /api/assets/backup/list` 的业务载荷（后端：`{ok, backups, count}`）。 */
interface AssetsBackupList {
  ok?: boolean
  backups?: BackupItem[]
  count?: number
}

interface AssetsPageProps {
  /** 初始分类（默认 memory）；由工作台导航 key 推导（assets/<category>），配合重挂载切换视图 */
  initialCategory?: string
}

export default function AssetsPage({ initialCategory = 'memory' }: AssetsPageProps) {
  const [overview, setOverview] = useState<Record<string, number>>({})
  const [active, setActive] = useState(initialCategory)
  const [items, setItems] = useState<AssetItem[]>([])
  const [backups, setBackups] = useState<BackupItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [newTitle, setNewTitle] = useState('')

  const loadOverview = () => {
    hubGet<AssetsOverview>(ASSETS_OVERVIEW).then((r) => {
      // 兼容旧形态（未装信封时后端直接把 overview 摊在顶层），但**不再靠猜**：
      // 两个候选键都显式写出，取不到就是空对象（与迁移前行为一致）。
      const ov = r?.overview ?? (r as unknown as Record<string, number>)
      setOverview(ov ?? {})
    }).catch(() => {})
  }

  const loadList = (cat: string) => {
    setLoading(true)
    hubGet<AssetsList>(assetsByCategory(cat)).then((r) => {
      setItems(r?.items ?? [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  const loadBackups = () => {
    hubGet<AssetsBackupList>(ASSETS_BACKUP_LIST).then((r) => {
      setBackups(r?.backups ?? [])
    }).catch(() => {})
  }

  useEffect(() => {
    loadOverview()
    loadList(active)
    loadBackups()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 初载一次；tab 切换由 switchCat 刷新
  }, [])

  const switchCat = (cat: string) => {
    setActive(cat)
    loadList(cat)
  }

  const addItem = async () => {
    if (!newTitle.trim()) return
    try {
      await hubPost(assetsByCategory(active), { title: newTitle.trim() })
      setNewTitle('')
      loadList(active)
      loadOverview()
    } catch (e) { setError(String(e)) }
  }

  const delItem = async (id: string) => {
    try {
      // 【修真实缺陷】原先是 `POST /api/assets/<cat>/<id>/delete` ——
      // 后端只注册了 `DELETE /api/assets/<cat>/<id>`，该路径**根本不存在**（实测 404）。
      // 这条缺陷此前被 contract_diff 的 "/assets/" 过滤挡住，对拍与 stray 门禁都看不见它。
      await hubDelete(assetById(active, id))
      loadList(active)
      loadOverview()
    } catch (e) { setError(String(e)) }
  }

  const backup = async () => {
    try {
      // 【修真实缺陷】`hubPost` 对**空 body 刻意不设 Content-Type**（见 ui.tsx 注释），
      // 而后端 `api_assets_backup` 直接 `request.get_json() or {}` ⇒ 抛 415，
      // 又被它的 `except Exception` 吞成 **500**（实测）。显式传 `{}` 即带上 JSON 头。
      const r = await hubPost(ASSETS_BACKUP, {})
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
