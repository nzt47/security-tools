/**
 * LLM 技能库 —— 技能启停 / 参数配置（提示/行为/扩展类技能，由 LLM 执行）
 * 数据源：/api/skills、/api/skills/toggle、/api/skills/params
 *
 * 说明：本页面向「LLM 技能」（注入每次 LLM 调用的提示/行为/扩展技能）。
 * 确定性、本地执行的「工作流技能」不在此列（见技能中心 → 工作流技能 Tab）。
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Layers, Loader2, Power } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubGet, hubPost, pickList } from '../components/ui'
import { getApiToken } from '../../../lib/apiToken'
import ApiTokenPrompt from './api-token-prompt'
import ClassIcon from './class-icon'

interface Skill {
  id: string
  name: string
  enabled: boolean
  description?: string
  params?: Record<string, unknown>
  /** 自动分类（/api/skills 由分类引擎实时给出：种子类/自动新建类/未分类） */
  class_name?: string
  /** 该分类是否为「自动新建类」（名称不在种子表，由新技能触发创建） */
  class_auto?: boolean
}

/** 运行时写接口（启停/补说明）需要 FLASK_API_TOKEN：401 → 显示令牌引导框 */
function tokenOrHint(e: unknown, setError: (s: string) => void, needAuth: (b: boolean) => void) {
  const m = e instanceof Error ? e.message : String(e)
  if (m.includes('401') || m.includes('未授权')) {
    needAuth(true)
    setError('')
  } else {
    needAuth(false)
    setError(m)
  }
}

/** 列表体（技能中心「LLM 技能」Tab 复用；页面/中心各自提供外壳） */
export function MemorySkillsTable() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [needAuth, setNeedAuth] = useState(false)
  /** 视图：flat=表格 / group=按自动分类折叠 */
  const [grouped, setGrouped] = useState(true)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [clsBusy, setClsBusy] = useState(false)

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
      await hubPost('/api/skills/toggle', { id }, getApiToken())
      load()
    } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
  }

  /** 手工补中文说明（写入覆盖层 data/skills_descriptions_overlay.json） */
  const describe = async (s: Skill) => {
    const text = window.prompt(`为「${s.name || s.id}」补中文说明（覆盖层持久化，缺描述时才显示）：`, '')
    if (text == null) return
    if (!text.trim()) { setInfo('已取消（说明为空不保存）。'); return }
    try {
      await hubPost('/api/skills/describe', { id: s.id, description: text.trim() }, getApiToken())
      setInfo(`已为「${s.name || s.id}」补写中文说明。`)
      load()
    } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
  }

  /** 自动补全已知内置技能的缺省中文说明（自省反思/邮件/记忆摘要等） */
  const autoDescribe = async () => {
    try {
      const r = await hubPost<{ count?: number }>('/api/skills/describe/auto', undefined, getApiToken())
      setInfo(`自动补全完成（${r?.count ?? 0} 项）。仍缺描述的（如 mock 测试项）可手工补写或删除。`)
      load()
    } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
  }

  /** 一键自动归类：为运行时「未分类」重新判定（可自动新建类；人工移动保留） */
  const runAutoClassify = async () => {
    setClsBusy(true); setError('')
    try {
      const r = await hubPost<{ classified?: number; created_classes?: number }>(
        '/api/skills/classify/run-auto', undefined, getApiToken())
      setInfo(`运行时自动分类完成：重判 ${r?.classified ?? 0} 项，自动新建类 ${r?.created_classes ?? 0} 个。`)
      load()
    } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
    finally { setClsBusy(false) }
  }

  /** 按自动分类分组（同名类合并；无分类 → 未分类），组按成员数倒序 */
  const groups = useMemo(() => {
    const buckets = new Map<string, Skill[]>()
    for (const s of skills) {
      const cls = (s.class_name || '').trim() || '未分类'
      buckets.set(cls, [...(buckets.get(cls) ?? []), s])
    }
    return [...buckets.entries()]
      .map(([name, list]) => ({ name, list }))
      .sort((a, b) => b.list.length - a.list.length || a.name.localeCompare(b.name, 'zh'))
  }, [skills])
  const uncCount = groups.find((g) => g.name === '未分类')?.list.length ?? 0

  const allCollapsed = grouped && groups.every((g) => collapsed[g.name])
  const foldAll = (fold: boolean) => {
    const next: Record<string, boolean> = {}
    groups.forEach((g) => { next[g.name] = fold })
    setCollapsed(next)
  }
  const toggleGroup = (name: string) => setCollapsed((p) => ({ ...p, [name]: !p[name] }))

  /** 单行内容（表格/分组共用外观） */
  const skillCell = (r: Skill) => (
    <div className="max-w-[26rem]">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-slate-200">{r.name}</span>
        {r.class_name && (
          <span className="inline-flex items-center gap-0.5 rounded-full border border-cyan-800/50 bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-300" title={r.class_auto ? '自动新建类（该技能出现时自动创建）' : '自动分类（新技能自动归类/新建类）'}>
            <ClassIcon name={r.class_name} size={8} /> {r.class_name}
          </span>
        )}
        {r.class_auto && (
          <span className="inline-flex items-center gap-0.5 rounded-full border border-violet-800/60 bg-violet-500/10 px-1.5 py-0.5 text-[9px] text-violet-300">自动建类</span>
        )}
      </div>
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
      {!r.description && (
        <button type="button" onClick={() => describe(r)}
          className="mt-1 rounded-md border border-dashed border-amber-700/70 px-2 py-0.5 text-[10px] text-amber-300 hover:bg-amber-950/40"
          title="给该技能补写中文说明（覆盖层持久化）">
          + 补中文说明
        </button>
      )}
    </div>
  )
  const toggleBtn = (r: Skill) => (
    <button
      onClick={() => toggle(r.id)}
      className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs ${
        r.enabled ? 'bg-slate-800 text-slate-300 hover:bg-slate-700' : 'bg-emerald-600 text-white hover:bg-emerald-500'
      }`}
    >
      <Power size={12} /> {r.enabled ? '停用' : '启用'}
    </button>
  )

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-500">按自动分类折叠浏览；新技能出现自动归入相应类（无匹配时自动新建类）</span>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" onClick={() => setGrouped(!grouped)}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] ${grouped ? 'border-cyan-700/60 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
            <Layers size={11} /> {grouped ? '按分类折叠中' : '按分类折叠'}
          </button>
          {grouped && skills.length > 0 && (
            <button type="button" onClick={() => foldAll(!allCollapsed)} className="rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800">
              {allCollapsed ? '全部展开' : '全部折叠'}
            </button>
          )}
          {grouped && uncCount > 0 && (
            <button type="button" onClick={() => void runAutoClassify()} disabled={clsBusy}
              title="为「未分类」的运行时技能重新自动归类（可自动新建类；人工移动过的不动）"
              className="flex items-center gap-1.5 rounded-md border border-violet-700/60 bg-violet-500/10 px-2.5 py-1 text-[11px] text-violet-300 hover:bg-violet-500/20 disabled:opacity-50">
              {clsBusy ? <Loader2 size={11} className="animate-spin" /> : <Layers size={11} />} 一键归类未分类({uncCount})
            </button>
          )}
          <button type="button" onClick={() => void autoDescribe()}
            className="flex items-center gap-1.5 rounded-md border border-cyan-700/60 px-2.5 py-1 text-[11px] text-cyan-300 hover:bg-cyan-500/10">
            <Power size={11} /> 自动补全中文说明
          </button>
          <button type="button" onClick={load} title="刷新运行时技能清单"
            className="rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800">
            刷新
          </button>
        </div>
      </div>
      {needAuth && <ApiTokenPrompt onSaved={() => { setNeedAuth(false); setInfo('已保存令牌，重试成功。'); load() }} />}
      {info && <div className="mb-2 rounded-md border border-cyan-900/60 bg-cyan-950/30 px-2 py-1 text-[11px] text-cyan-300">{info}</div>}
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : skills.length === 0 ? (
        <Card><div className="py-6 text-center text-xs text-slate-500">暂无技能。</div></Card>
      ) : grouped ? (
        <div className="space-y-2">
          {groups.map((g) => {
            const open = !collapsed[g.name]
            return (
              <div key={g.name} className="overflow-hidden rounded-lg border border-slate-800">
                <button type="button" onClick={() => toggleGroup(g.name)}
                  className="flex w-full items-center gap-2 border-b border-slate-800/70 bg-slate-900/60 px-3 py-2 text-left hover:bg-slate-900"
                  title={open ? '折叠该类' : '展开该类'}>
                  {open ? <ChevronDown size={13} className="shrink-0 text-slate-400" /> : <ChevronRight size={13} className="shrink-0 text-slate-400" />}
                  <ClassIcon name={g.name} size={13} className="shrink-0 text-cyan-400" />
                  <span className="text-xs font-medium text-slate-100">{g.name}</span>
                  <span className="rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">{g.list.length}</span>
                  <span className="ml-auto hidden text-[10px] text-slate-600 sm:inline">同类折叠 · 新技能自动归类</span>
                </button>
                {open && (
                  <div className="divide-y divide-slate-800/50">
                    {g.list.map((r) => (
                      <div key={r.id} className="flex items-start justify-between gap-3 bg-slate-950/40 px-3 py-2 hover:bg-slate-900/40">
                        {skillCell(r)}
                        <div className="flex shrink-0 flex-col items-end gap-1.5">
                          <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge>
                          {toggleBtn(r)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <Card>
          <DataTable
            data={skills}
            keyField="id"
            columns={[
              { key: 'name', title: '技能 / 触发方式', render: skillCell },
              { key: 'enabled', title: '状态', render: (r) => (
                <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge>
              ) },
              { key: 'actions', title: '操作', render: toggleBtn },
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

