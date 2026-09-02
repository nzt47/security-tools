/**
 * 知识库系统 —— 卡片 CRUD / 索引 / 图
 * 数据源：/api/knowledge/cards、/api/knowledge/index、/api/knowledge/graph
 */
import { useEffect, useState } from 'react'
import { BookOpen, RefreshCw } from 'lucide-react'
import {  Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, unwrap , pickList } from '../components/ui'

interface KnowledgeCard {
  slug: string
  title?: string
  tags?: string[]
  category?: string
  updated_at?: string
  [k: string]: unknown
}

export default function MemoryKnowledge() {
  const [cards, setCards] = useState<KnowledgeCard[]>([])
  const [indexInfo, setIndexInfo] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      hubGet('/api/knowledge/cards').then((r) => pickList<KnowledgeCard>(r, 'cards')),
      hubGet('/api/knowledge/index').then((r) => unwrap<Record<string, unknown>>(r as Record<string, unknown>) ?? {}),
    ]).then(([c, i]) => {
      if (c.status === 'fulfilled') setCards(c.value)
      if (i.status === 'fulfilled') setIndexInfo(i.value)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  return (
    <div className="p-6">
      <PageHeader
        title="知识库系统"
        description="知识卡片管理、索引与图谱"
        actions={<button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"><RefreshCw size={12} /> 刷新</button>}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <>
          {indexInfo && (
            <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
              <Card title="卡片总数" className="col-span-1"><div className="text-2xl font-semibold text-white">{cards.length}</div></Card>
              {Object.entries(indexInfo).slice(0, 3).map(([k, v]) => (
                <Card key={k} title={k} className="col-span-1">
                  <div className="text-2xl font-semibold text-white">{String(v ?? '-')}</div>
                </Card>
              ))}
            </div>
          )}
          <Card title={`知识卡片（${cards.length}）`}>
            <DataTable
              data={cards}
              keyField="slug"
              columns={[
                { key: 'title', title: '标题', render: (r) => <span className="text-slate-200">{String(r.title ?? r.slug)}</span> },
                { key: 'category', title: '分类', render: (r) => (r.category ? <Badge color="cyan">{String(r.category)}</Badge> : '-') },
                {
                  key: 'tags', title: '标签',
                  render: (r) => (
                    <div className="flex flex-wrap gap-1">
                      {(r.tags ?? []).slice(0, 4).map((t: unknown, i: number) => (
                        <span key={i} className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400">{String(t)}</span>
                      ))}
                    </div>
                  ),
                },
                { key: 'updated_at', title: '更新时间', render: (r) => <span className="font-mono text-xs text-slate-500">{String(r.updated_at ?? '')}</span> },
              ]}
            />
          </Card>
        </>
      )}
    </div>
  )
}
