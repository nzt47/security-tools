/**
 * LLM 技能库 —— 技能启停 / 参数配置（提示/行为/扩展类技能，由 LLM 执行）
 * 数据源：/api/skills、/api/skills/toggle、/api/skills/params
 *         + /api/capability-manifest（技能侧「可被 LLM 调用」统一标注，只读、独立降级）
 *
 * 说明：本页面向「LLM 技能」（注入每次 LLM 调用的提示/行为/扩展技能）。
 * 确定性、本地执行的「工作流技能」不在此列（见技能中心 → 工作流技能 Tab）。
 *
 * 【为什么技能行上要标可调用性】
 *   技能**不是**模型发起的工具调用：它们由 ContextInjector 按意图注入上下文、
 *   或由 SkillExecutor 显式执行，故清单里一律是 ❌ 不可调用，`reason` 写明原因。
 *   把这条如实显示出来，才能消除"技能库里有 = 模型能自己调"的误解。
 *   数据取 `manifest.skills`（与工具同构的八字段 + `mark`）；清单取不到时徽章与图例
 *   整体不渲染，本页功能不受影响。
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Eye, Layers, Loader2, Power } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, CallabilityBadge, PageHeader, hubGet, hubPost, pickList } from '../components/ui'
import {
  callabilityCounts, callabilityLegend, callabilityMark, fetchCapabilityManifest,
  indexCallability,
  type CallabilityInfo,
} from '@/lib/callability'
import { getApiToken } from '../../../lib/apiToken'

/** 「恢复自动分类」的哨兵值（提交时转成 `auto: true`，不是真实分类名） */
const AUTO_CLASS = '__auto__'
/** 未分类的类名（与后端 `categorizer.UNCLASSIFIED` 同字面量；改类时可作为目标） */
const UNCLASSIFIED = '未分类'
import ApiTokenPrompt from './api-token-prompt'
import SkillContentModal from './skill-content-modal'
import ClassIcon from './class-icon'

interface Skill {
  id: string
  name: string
  enabled: boolean
  /** 英文描述（唯一事实源 = skill.md front matter；检索 + 模型可见用） */
  description?: string
  /** 中文展示文案（skill.md front matter 的 description_zh；UI 优先读它）。
   *  【G1-B/M4】合并视图 as_legacy_rows() 自本卡起 description 取**文件轨优先**，
   *  UI 必须同批改读 description_zh，否则 15 条 pd-* 的文案会从中文变英文。 */
  description_zh?: string
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
  /** 行内「查看技能具体内容」的目标行 */
  const [viewSkill, setViewSkill] = useState<Skill | null>(null)
  /** 视图：flat=表格 / group=按自动分类折叠 */
  const [grouped, setGrouped] = useState(true)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [clsBusy, setClsBusy] = useState(false)
  /** 技能可调用性标注：{技能 id: 标注}（清单不可用 ⇒ 空表 ⇒ 不显示徽章/图例） */
  const [callability, setCallability] = useState<Record<string, CallabilityInfo>>({})
  const [callabilityNote, setCallabilityNote] = useState('')
  /** 端点请求期补算的运行时技能条数（清单文件里没有它们：仓库里无 skill.md 实体） */
  const [runtimeSkillCount, setRuntimeSkillCount] = useState(0)
  /** 可选分类名（种子类 + 已自动建类 + 未分类），供「改类」下拉；取不到则不显示入口 */
  const [classNames, setClassNames] = useState<string[]>([])
  /** 正在改类的技能 id（非空时该行展开下拉） */
  const [movingId, setMovingId] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    hubGet('/api/skills').then((r) => {
      const installed = pickList<Skill>(r, 'installed')
      const available = pickList<Skill>(r, 'available')
      setSkills(installed.length > 0 ? installed : available)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })

    // 可调用性清单——独立降级：拿不到就没有标识，绝不影响技能列表本身
    fetchCapabilityManifest().then((r) => {
      // 仓库口径（清单文件）覆盖运行时口径（端点请求期补算的运行时技能）
      setCallability(indexCallability(r?.runtime_skills, r?.manifest?.skills))
      setRuntimeSkillCount((r?.runtime_skills ?? []).length)
      setCallabilityNote((r?.manifest?.vocabulary?.mark ?? []).join(' / '))
    }).catch(() => { setCallability({}); setRuntimeSkillCount(0); setCallabilityNote('') })

    // 可选分类名——独立降级：拿不到就不显示「改类」入口（改类是人工兜底，非主路径）
    // 复用既有端点 `/api/skills-mgmt/classes`（技能中心同款；hubGet 会自动附带本地令牌）
    hubGet<{ ok?: boolean; groups?: { name?: string }[] }>('/api/skills-mgmt/classes').then((r) => {
      const names = (r?.groups ?? []).map((g) => String(g?.name ?? '')).filter(Boolean)
      setClassNames(names.includes(UNCLASSIFIED) ? names : [...names, UNCLASSIFIED])
    }).catch(() => setClassNames([]))
  }

  useEffect(load, [])

  const toggle = async (id: string) => {
    try {
      await hubPost('/api/skills/toggle', { id }, getApiToken())
      load()
    } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
  }

  /**
   * 人工改类：把技能钉在指定分类上（`manual` 语义 ⇒ 之后自动重判/内容更新都不再改动它）
   *
   * 规则分类是关键词打分，遇到"通用词夺域"或"多路并列按表序决胜"时会给出不合理结果
   * （实证：易之三义 曾判成语音与多媒体），个案只能人工指定 —— 本入口就是那个兜底。
   */
  const moveClass = async (id: string, cls: string) => {
    setMovingId(null)
    // 复用既有端点 `/api/skills-mgmt/classes/move`（技能中心同款，单一权威；不新增第二份口径）
    if (cls === AUTO_CLASS) {                       // 恢复自动：解除人工钉住
      try {
        const r = await hubPost<{ ok?: boolean; released?: string[]; error?: string }>(
          '/api/skills-mgmt/classes/move', { skill_id: id, auto: true }, getApiToken())
        if (r?.ok === false) { setInfo(`恢复自动失败：${r.error ?? ''}`); return }
        setInfo(`已解除「${id}」的人工钉住（${(r?.released || []).join(' / ')}）：`
          + '归类暂保持不变，内容域变化时按规则自动跟随')
        load()
      } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
      return
    }
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; class_name?: string }>(
        '/api/skills-mgmt/classes/move', { skill_id: id, class_name: cls }, getApiToken())
      if (r?.ok === false) { setInfo(`改类失败：${r.error ?? ''}`); return }
      setInfo(`已把「${id}」移动到「${r?.class_name || cls}」并钉住（自动重判不再改动它）`)
      load()
    } catch (e) { tokenOrHint(e, setError, setNeedAuth) }
  }

  /** 手工补中文说明 —— 【G1-B/M0 已冻结】
   *  写入目标是 data/skills_descriptions_overlay.json（覆盖层），该写路径已随
   *  POST /api/skills/describe 一起冻结（409）。描述唯一事实源是 skill.md 的
   *  front matter，故这里不再发起写入，只把正确入口告诉使用者。 */
  const describe = async (s: Skill) => {
    const text = window.prompt(`为「${s.name || s.id}」补中文说明（覆盖层持久化，缺描述时才显示）：`, '')
    if (text == null) return
    // 无论是否填了内容都不再发请求：后端该入口已冻结（写路径先冻、再删数据）
    setInfo(`描述写入入口已冻结：请在 data/skills_repo/${s.id}/skill.md 的 `
      + `front matter 写 description（英文，检索用）与 description_zh（中文，界面用）。`)
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

  /** 本页技能的三档标识计数（技能侧通常全为 ❌：由注入器/执行器触发，非模型发起的调用） */
  const skillMarksText = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const s of skills) {
      const mark = callabilityMark(callability[s.id])
      if (mark) counts[mark] = (counts[mark] ?? 0) + 1
    }
    return callabilityCounts(counts)
  }, [skills, callability])
  const showCallability = skillMarksText !== '' || callabilityNote !== ''
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
        {/* 可调用性标识（来自 /api/capability-manifest 的 manifest.skills）：mark 缺失即不渲染 */}
        <CallabilityBadge info={callability[r.id]} name={r.name || r.id} />
        {/* 人工改类：规则分类不合适时的兜底（钉住后自动重判不再改动） */}
        {classNames.length > 0 && (
          movingId === r.id ? (
            <select
              autoFocus
              defaultValue={r.class_name || '未分类'}
              onChange={(e) => moveClass(r.id, e.target.value)}
              onBlur={() => setMovingId(null)}
              className="rounded border border-cyan-700/60 bg-slate-900 px-1 py-0.5 text-[10px] text-cyan-200"
              title="选择目标分类（人工移动后自动重判不再改动该技能）；选「恢复自动分类」可解除钉住"
            >
              {classNames.map((c) => <option key={c} value={c}>{c}</option>)}
              <option value={AUTO_CLASS}>恢复自动分类（解除钉住）</option>
            </select>
          ) : (
            <button
              type="button"
              onClick={() => setMovingId(r.id)}
              className="rounded-full border border-slate-700 px-1.5 py-0.5 text-[9px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              title="人工改类：把该技能钉在指定分类上（关键词打分不合适时的兜底；钉住后自动重判不再改动）"
            >
              改类
            </button>
          )
        )}
      </div>
      {/* 【G1-B/M4】优先读 description_zh（中文），回落 description（英文）：
          合并视图的 description 已改为文件轨优先（= 英文原文），若这里不改读 zh，
          15 条 pd-* 的中文说明会在切换瞬间变成英文（G1-A 风险 R1 的唯一用户可见破坏）。 */}
      {(r.description_zh || r.description) && (
        <div className="text-xs text-slate-500">{r.description_zh || r.description}</div>
      )}
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
      {!(r.description_zh || r.description) && (
        <button type="button" onClick={() => describe(r)}
          className="mt-1 rounded-md border border-dashed border-amber-700/70 px-2 py-0.5 text-[10px] text-amber-300 hover:bg-amber-950/40"
          title="该技能缺描述。描述唯一事实源是 data/skills_repo/&lt;id&gt;/skill.md 的 front matter（description 英文 / description_zh 中文）；写入入口已冻结">
          + 补中文说明（入口已冻结）
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
  /** 行内「查看技能具体内容」按钮（弹出 GET /api/skills/content 解析后的正文） */
  const viewBtn = (r: Skill) => (
    <button
      type="button"
      onClick={() => setViewSkill(r)}
      className="flex items-center gap-1.5 rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800 hover:text-slate-200"
      title="查看该技能的具体内容（注入正文 / 脚本 / 参数）"
    >
      <Eye size={12} /> 查看内容
    </button>
  )
  /** 行操作组（查看内容 + 启停），表格与分组视图共用 */
  const rowActions = (r: Skill) => (
    <div className="flex items-center gap-1.5">
      {viewBtn(r)}
      {toggleBtn(r)}
    </div>
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
      {showCallability && (
        <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
          <span className="text-slate-400">可调用性标识</span>
          <span>{callabilityLegend(callabilityNote)}</span>
          {/* 运行时技能（仓库无 skill.md 实体）：标注由端点请求期补算，如实提示口径 */}
          {runtimeSkillCount > 0 && (
            <span className="rounded border border-slate-700 px-1 text-slate-400">
              其中 {runtimeSkillCount} 条由运行时目录/台账补算
            </span>
          )}
          {skillMarksText && <span className="ml-auto">{skillMarksText}</span>}
        </div>
      )}
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
                          {rowActions(r)}
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
              { key: 'actions', title: '操作', render: rowActions },
            ]}
          />
        </Card>
      )}

      {viewSkill && <SkillContentModal skill={viewSkill} onClose={() => setViewSkill(null)} />}
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

