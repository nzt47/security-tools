/**
 * LLM 技能库 —— 技能启停 / 参数配置（提示/行为/扩展类技能，由 LLM 执行）
 * 数据源：/api/skills、/api/skills/toggle、/api/skills/params
 *
 * 说明：本页面向「LLM 技能」（注入每次 LLM 调用的提示/行为/扩展技能）。
 * 确定性、本地执行的「工作流技能」不在此列（见技能中心 → 工作流技能 Tab）。
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

/** 列表体（技能中心「LLM 技能」Tab 复用；页面/中心各自提供外壳） */
export function MemorySkillsTable() {
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
    <div>
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={skills}
            keyField="id"
            columns={[
              { key: 'name', title: '技能 / 触发方式', render: (r) => (
                <div className="max-w-[26rem]">
                  <div className="font-medium text-slate-200">{r.name}</div>
                  {r.description && <div className="text-xs text-slate-500">{r.description}</div>}
                  {/* 触发方式：运行时按意图语义匹配命中后注入上下文 */}
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] ${r.enabled ? 'border-emerald-800/70 bg-emerald-500/10 text-emerald-400' : 'border-slate-700 bg-slate-500/10 text-slate-400'}`}>
                      {r.enabled ? '注入候选中' : '停用·不触发'}
                    </span>
                    <span className="inline-flex items-center rounded-full border border-cyan-800/60 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-300" title="运行时用 名称/描述/标签/内容 与用户意图做相似度匹配，命中即注入元数据→按需加载指令/少样本">
                      触发=语义匹配(名称/描述/标签/内容)
                    </span>
                    {r.params && typeof r.params === 'object' && Object.keys(r.params).length > 0 && (
                      <span className="inline-flex items-center rounded-full border border-indigo-800/60 bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300" title="该技能带可配置参数（脚本/扩展技能），命中后按参数执行">
                        {Object.keys(r.params).length} 参数
                      </span>
                    )}
                  </div>
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

export default function MemorySkills() {
  return (
    <div className="p-6">
      <PageHeader title="LLM 技能库" description="提示/行为/扩展类技能（由 LLM 执行）的启用、停用与参数配置" />
      <MemorySkillsTable />
    </div>
  )
}
