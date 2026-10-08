/**
 * 装配车间 —— 分身创建与组装（多 agent 设计系统）
 * ------------------------------------------------------------------
 * 【2026-10-08 合并：本页成为「分身装配」的唯一入口】三个视图：
 *   ① 分身        —— 存活分身的创建 / 销毁（`/api/subagent/*`），本页原有内容；
 *   ② 主线档案    —— 能力平面档案（权重/保底/效果上限/技能包/提示词）+ 实时装配预览，
 *                     由原「工具调用 → 主线管理」Tab 迁入（`pages/hub/tools/lines.tsx`）；
 *   ③ 主线 × 四面 —— 一条主线在「工具 / 技能 / 提示词 / 分身」四个面上的横向对比，
 *                     由原 `features/line-assembly`（零清单导航项）并入，并**删除其 manifest**
 *                     （留着它会凭空多出一条顶层导航项 = 同一个页面两个入口）。
 *
 * 【为什么必须合并（不是挪 UI 而已）】分身真正拿到什么由**主线档案**决定：
 * `agent/subagent/assembly.py::resolve_subagent_assembly` 按 line 装配工具集，再由
 * `agent/subagent/toolset.py`（§7.0 矩阵 + §5.7 机制 3）收紧。而本页「创建分身」表单里的
 * `model_id` / `memory_provider` / `tool_sources` 三个字段**目前没有任何运行时消费者**
 * （只进容器状态与热更新变更日志）。把"表单承诺"与"真正决定分身能力的主线档案"分在
 * 两个导航栏目里，是这一页最容易误导人的地方 —— 合并后同屏，且不再暗示表单那几个字段
 * 已经生效（真正的 per-分身 模型/记忆接线见后续阶段，届时同页给出"生效来源"）。
 *
 * 【Tab 选择记忆】与「工具调用」Tab 容器同款：localStorage 记住上次视图，非法值回落
 * 「分身」。注意两个 key 是**分开的**（`yunshu.workshop.view` 与本页无关的
 * `yunshu.tools.tab`）：原「工具调用 → 主线管理」Tab 被摘掉后，那边记忆里残留的
 * `'lines'` 会在**那边**回落成「工具集」（由 `tools/index.test.tsx` 钉住），
 * 不会漂移到本页；本页视图 id 复用 `'lines'` 只是名字相同，两处互不影响。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Layers, Loader2, Plus, Rocket, Route, Trash2, Users } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubPost } from '../components/ui'

import {
  SUBAGENT_CREATE,
  SUBAGENT_LIST,
  subagentDestroyByName,
} from '@/api/endpoints';
// 【P1-front 第三批】后端 GET /api/subagent/list 已带 X-Envelope: v2，改用显式信封解析。
import { getEnvelope } from '@/api/envelope';

interface Subagent {
  name: string
  model_id?: string
  memory_provider?: string
  status?: string
  tool_sources?: string[]
  tags?: string[]
  /** 后端补的"实际会用哪个模型"（三档来源，见 agent/subagent/llm_factory.py） */
  llm?: SubagentLlm
  /** 后端补的"角色生效情况"（受控模板 / 档位 / 红档，见 agent/subagent/role_templates.py） */
  role?: SubagentRole
  [k: string]: unknown
}

/** 逐分身的模型生效情况（后端 `resolve_subagent_llm(...).to_dict()`） */
interface SubagentLlm {
  /** 配置里点的模型名（空 = 跟随母体） */
  requested?: string
  /** **实际生效**的模型名（取自实例本身，不取配置） */
  model?: string
  /** inherit=跟随母体 / explicit=指定且已派生 / fallback-after-error=指定未生效已回退 */
  source?: string
  error?: string
  /** 实际生效的生成温度；null/undefined = **未干预**执行器默认（不是 0.0） */
  temperature?: number | null
}

/** 逐分身的角色生效情况（后端 `resolve_subagent_role(...).to_dict()`，**不含自由文本正文**） */
interface SubagentRole {
  /** 生效的受控模板 id；空 = 未装角色（system prompt 与改动前逐字相同） */
  template?: string
  /** template / template+text / full-system */
  tier?: string
  /** 片段来源（如 role_template:code_review；未装角色为空） */
  source?: string
  /** 角色片段字符数（不是配置里那串声明的长度） */
  fragment_chars?: number
  /** 追加进 ②约束 的行数（template+text 档为 1，其余为 0） */
  constraints?: number
  /** 该档位是否必须留审计（非默认档 = true） */
  audit_required?: boolean
  /** 是否红档（full-system：自由文本进 system prompt） */
  red?: boolean
  /** 解析失败原因（非空 = 这份配置在委派时会被拒） */
  error?: string
}

/** 部署级角色事实（`GET /api/subagent/list` 的 `role` 段，后端 `role_catalog()`） */
interface RoleCatalog {
  templates?: { id?: string; title?: string; body?: string; note?: string }[]
  tiers?: { value?: string; label?: string; semantics?: string; red?: boolean; audit?: boolean }[]
  default_tier?: string
  role_text_max_chars?: number
}

/** 部署级 LLM 事实 + 可选模型清单（`GET /api/subagent/list` 的 `llm` 段） */
interface DeploymentLlm {
  model?: string
  provider?: string
  /** 只列"有出处"的模型名（部署默认 + 已声明），**不是**一份模型目录 */
  options?: { model?: string; source?: string }[]
}

/** `GET /api/subagent/list` 的业务载荷（后端：`{ok, subagents, count, channel, llm}`）。
 *
 * `count` / `channel` / `llm` 本页此前不读，但**必须**在类型里保留 ——
 * 它们是后端契约的一部分（`channel` 用于"通道不可用时提前提示"，`llm` 用于
 * "这个分身实际跑哪个模型 + 我能填哪些模型名"），前端不读不等于可以删。
 */
interface SubagentList {
  ok?: boolean
  subagents?: Subagent[]
  count?: number
  channel?: Record<string, unknown>
  llm?: DeploymentLlm
  role?: RoleCatalog
}

/** 模型来源徽章（三档与后端 `source` 一一对应；"未生效"必须是红档，不许静默） */
function LlmSourceBadge({ llm }: { llm?: SubagentLlm }) {
  if (!llm) return null
  const source = String(llm.source || '')
  if (source === 'explicit') return <Badge color="green">指定模型</Badge>
  if (source === 'inherit') return <Badge color="slate">跟随母体</Badge>
  return <Badge color="red">指定未生效</Badge>
}

/** 角色档位徽章（默认档 = 受控模板；红档 = full-system，自由文本进 system prompt） */
function RoleBadge({ role }: { role?: SubagentRole }) {
  if (!role) return null
  if (role.error) return <Badge color="red">角色配置无效</Badge>
  if (!role.template) return <Badge color="slate">未装角色</Badge>
  if (role.red) return <Badge color="red">红档·自由文本进系统提示词</Badge>
  if (role.tier === 'template+text') return <Badge color="amber">模板+文本（进约束）</Badge>
  return <Badge color="slate">受控模板</Badge>
}

/** 后端不可用时的档位兜底（与 role_templates.ROLE_TIERS 同序同值；正常走 roleCatalog） */
const DEFAULT_ROLE_TIERS = [
  { value: 'template', label: '受控模板', red: false },
  { value: 'template+text', label: '模板 + 自由文本（进约束）', red: false },
  { value: 'full-system', label: '自由文本进系统提示词（红档）', red: true },
]

/** 视图键（= 原导航/Tab 身份，便于对照历史记录） */
export type WorkshopView = 'agents' | 'lines' | 'line-assembly'

export interface WorkshopViewDef {
  id: WorkshopView
  label: string
  icon: LucideIcon
  hint: string
}

/** 三个视图：顺序 = 「先有分身 → 再定它拿到什么 → 再看四个面」的阅读顺序 */
export const WORKSHOP_VIEWS: WorkshopViewDef[] = [
  { id: 'agents', label: '分身', icon: Users, hint: '存活分身的创建与销毁（模型 / 记忆提供商为声明字段，见页面内说明）' },
  { id: 'lines', label: '主线档案', icon: Route, hint: '能力平面档案（权重 / 核心工具 / 效果上限 / 技能包 / 提示词）与实时装配预览' },
  { id: 'line-assembly', label: '主线 × 四面', icon: Layers, hint: '一条主线在工具 / 技能 / 提示词 / 分身四个面上的横向对比' },
]

/** 视图选择记忆（会话级偏好；刷新/重开不跳回第一个视图） */
export const WORKSHOP_VIEW_STORAGE_KEY = 'yunshu.workshop.view'

export const isWorkshopView = (v: unknown): v is WorkshopView =>
  typeof v === 'string' && WORKSHOP_VIEWS.some((t) => t.id === v)

function readSavedView(): WorkshopView {
  try {
    // 两个 key 分开：本页的视图记忆与「工具调用」的 Tab 记忆互不影响
    // （那边残留的 'lines' 在那边回落「工具集」，见 tools/index.test.tsx）
    const v = localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)
    return isWorkshopView(v) ? v : 'agents'
  } catch {
    return 'agents'
  }
}

const AgentLinesView = lazy(() => import('./agent-lines'))
const LineAssemblyView = lazy(() => import('./line-assembly'))

export interface WorkshopAgentsProps {
  /** 初始视图（缺省用 localStorage 记忆的值；非法值回落「分身」） */
  initialView?: WorkshopView
}

// ═══════════════════════════════════════════════════════════
//  ① 分身：创建 / 列表 / 销毁（本页原有内容，一字未改语义）
// ═══════════════════════════════════════════════════════════

function SubagentView() {
  const [agents, setAgents] = useState<Subagent[]>([])
  const [deployment, setDeployment] = useState<DeploymentLlm | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  // 空 = 跟随母体（后端真语义；不再靠"抄一个母体模型名"来伪装跟随）
  const [model, setModel] = useState('')
  // 空 = 不干预执行器默认（不是 0.0）；填了则必须 0.0–2.0（越界后端 400）
  const [temperature, setTemperature] = useState('')
  const [memory, setMemory] = useState('default')
  const [tools, setTools] = useState('')
  // 角色：受控模板 id + 档位 + 自由文本。默认档（template）下自由文本会被后端 400 ——
  // 这是刻意的：'没显式选档' ≠ '自由文本生效'，前端不替使用者静默选档。
  const [roleCatalog, setRoleCatalog] = useState<RoleCatalog | null>(null)
  const [roleTemplate, setRoleTemplate] = useState('')
  const [roleMode, setRoleMode] = useState('template')
  const [roleText, setRoleText] = useState('')

  const load = () => {
    setLoading(true)
    getEnvelope<SubagentList>(SUBAGENT_LIST).then((d) => {
      setAgents(d?.subagents ?? [])
      setDeployment(d?.llm ?? null)
      setRoleCatalog(d?.role ?? null)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const create = async () => {
    try {
      const body: Record<string, unknown> = {
        name,
        model_id: model.trim(),  // 空串 = 跟随母体（后端按 inherit 档解析）
        memory_provider: memory,
        tool_sources: tools ? tools.split(',').map((t) => t.trim()).filter(Boolean) : [],
        tags: ['hub'],
        // 角色（受控模板）：模板 id + 档位恒发；自由文本留空则**不发这个键**
        // （默认档下发非空 role_text 后端会 400 —— 那正是'要显式选档'的纪律）
        role_template: roleTemplate.trim(),
        role_mode: roleMode,
      }
      // 温度：留空**不发这个键**（= 不干预执行器默认），而不是发 0
      const t = temperature.trim()
      if (t) body.llm_temperature = Number(t)
      const rt = roleText.trim()
      if (rt) body.role_text = rt
      await hubPost(SUBAGENT_CREATE, body)
      setShowForm(false)
      setName(''); setTools(''); setTemperature('')
      setRoleTemplate(''); setRoleText(''); setRoleMode('template')
      load()
    } catch (e) { setError(String(e)) }
  }

  const destroy = async (n: string) => {
    try {
      await hubPost(subagentDestroyByName(n))
      load()
    } catch (e) { setError(String(e)) }
  }

  const deploymentModel = String(deployment?.model || '')
  // 档位候选来自后端（与 role_templates.ROLE_TIERS 同源）；后端不可用时用兜底常量
  const roleTiers = roleCatalog?.tiers?.length ? roleCatalog.tiers : DEFAULT_ROLE_TIERS

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs text-slate-500">
          工具集由「主线档案」决定（装配时去 govern、去 §5.7 机制 3 硬禁）；
          <span className="text-slate-400">模型已接线</span>
          （留空 = 跟随母体{deploymentModel ? `：${deploymentModel}` : '（母体模型未知）'}，
          指定则派生独立实例；生效来源见表中徽章）。
          <span className="text-slate-400">角色已接线</span>
          （受控模板：默认档只用词表正文；自由文本必须显式选 template+text / full-system 档，
          后者是红档且执行器写审计；生效档位见表中徽章）。
          记忆提供商 / 工具源仍是**声明字段**（尚未接线，见后续阶段）。
        </span>
        <button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"><Plus size={12} /> 创建分身</button>
      </div>
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {showForm && (
        <Card className="mb-4">
          <div className="grid gap-3 md:grid-cols-4">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="分身名称 *" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            {/* 模型：datalist 给"有出处"的候选（部署默认 + 已声明），**不挡手填** */}
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              list="subagent-model-options"
              data-testid="subagent-model-input"
              placeholder={deploymentModel ? `留空 = 跟随母体（${deploymentModel}）` : '留空 = 跟随母体'}
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
            />
            <datalist id="subagent-model-options">
              {(deployment?.options ?? []).map((o) => (
                <option key={String(o.model)} value={String(o.model ?? '')}>
                  {o.source === 'deployment' ? '部署默认' : '已声明'}
                </option>
              ))}
            </datalist>
            <input value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="记忆提供商" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={tools} onChange={(e) => setTools(e.target.value)} placeholder="工具源(逗号分隔)" className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            {/* 温度：留空 = 不干预执行器默认（**不是 0**）；越界由后端 400，前端不做静默夹取 */}
            <input
              value={temperature}
              onChange={(e) => setTemperature(e.target.value)}
              inputMode="decimal"
              data-testid="subagent-temperature-input"
              placeholder="生成温度 0.0–2.0（留空 = 默认）"
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
            />
            {/* 角色：模板候选来自后端受控词表（不是自由输入 id） */}
            <select
              value={roleTemplate}
              onChange={(e) => setRoleTemplate(e.target.value)}
              data-testid="subagent-role-template"
              title="受控角色模板（agent/subagent/role_templates.py 词表；空 = 未装角色=旧行为）"
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none"
            >
              <option value="">角色：未装（旧行为）</option>
              {(roleCatalog?.templates ?? []).map((t) => (
                <option key={String(t.id)} value={String(t.id)}>{String(t.title || t.id)}</option>
              ))}
            </select>
            {/* 档位：默认 template；自由文本必须显式选档，否则后端 400（不静默） */}
            <select
              value={roleMode}
              onChange={(e) => setRoleMode(e.target.value)}
              data-testid="subagent-role-mode"
              title="角色档位：template 默认 / template+text 自由文本进约束 / full-system 自由文本进系统提示词（红档）"
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none"
            >
              {roleTiers.map((t) => (
                <option key={String(t.value)} value={String(t.value)}>
                  {t.red ? '⚠ ' : ''}{String(t.label || t.value)}
                </option>
              ))}
            </select>
            <input
              value={roleText}
              onChange={(e) => setRoleText(e.target.value)}
              data-testid="subagent-role-text"
              placeholder="角色自由文本（仅显式选 template+text / full-system 档生效）"
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none md:col-span-2"
            />
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
              {
                key: 'model_id', title: '模型（实际生效）',
                render: (r) => {
                  const llm = r.llm
                  const effective = String(llm?.model || r.model_id || '')
                  const t = llm?.temperature
                  return (
                    <span className="flex flex-wrap items-center gap-1.5" data-testid={`subagent-llm-${String(r.name)}`}>
                      <span className="font-mono text-xs text-cyan-400" data-testid="subagent-llm-model">
                        {effective || '未解析'}
                      </span>
                      {/* 温度：null/undefined = 未干预（**不显示 T=0**，那会把"没表态"说成"最确定性"） */}
                      {typeof t === 'number' && (
                        <span className="font-mono text-[11px] text-slate-400" data-testid="subagent-llm-temperature">
                          T={t}
                        </span>
                      )}
                      <LlmSourceBadge llm={llm} />
                      {llm?.error && (
                        <span className="text-[10px] text-red-300" title={String(llm.error)}>
                          回退原因
                        </span>
                      )}
                    </span>
                  )
                },
              },
              {
                key: 'role', title: '角色',
                render: (r) => {
                  const role = r.role
                  const tpl = String(role?.template || '')
                  const title = (roleCatalog?.templates ?? []).find((t) => t.id === tpl)?.title
                  return (
                    <span className="flex flex-wrap items-center gap-1.5" data-testid={`subagent-role-${String(r.name)}`}>
                      <span className="text-xs text-slate-300">{tpl ? String(title || tpl) : '未装'}</span>
                      <RoleBadge role={role} />
                    </span>
                  )
                },
              },
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
    </>
  )
}

// ═══════════════════════════════════════════════════════════
//  容器：三视图 Tab
// ═══════════════════════════════════════════════════════════

export default function WorkshopAgents({ initialView }: WorkshopAgentsProps) {
  const [view, setView] = useState<WorkshopView>(
    () => (isWorkshopView(initialView) ? initialView : readSavedView()))

  useEffect(() => {
    try {
      localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, view)
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [view])

  const active = WORKSHOP_VIEWS.find((t) => t.id === view) ?? WORKSHOP_VIEWS[0]

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶栏：模块标题 + 三视图 Tab 条（子视图即 Tab，点击切换、无需回导航树） */}
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <Users size={14} className="text-cyan-400" />
          <h1 className="text-[15px] font-semibold text-slate-100">分身创建与组装</h1>
          <span className="text-[11px] text-slate-500">{active.hint}</span>
        </div>

        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="分身装配视图">
          {WORKSHOP_VIEWS.map((t) => {
            const Icon = t.icon
            const selected = view === t.id
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={selected}
                className={`flex items-center gap-1.5 rounded-t-lg border border-b-0 px-3 py-1.5 text-[12.5px] transition-colors ${
                  selected
                    ? 'border-slate-700 bg-slate-950 font-medium text-cyan-300'
                    : 'border-transparent text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
                }`}
                onClick={() => setView(t.id)}
                title={t.hint}
              >
                <Icon size={13} className={selected ? 'text-cyan-400' : 'text-slate-500'} />
                {t.label}
              </button>
            )
          })}
        </nav>
      </header>

      {/* 视图内容：key={view} 强制重挂载，避免切换后残留上一个视图的局部状态 */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-slate-950">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center gap-2 text-slate-500">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">加载中…</span>
            </div>
          }
        >
          {view === 'agents' && (
            <div className="p-6">
              <PageHeader
                title="分身创建与组装"
                description="多 agent 设计系统 —— 分身生命周期管理"
              />
              <SubagentView key="agents" />
            </div>
          )}
          {view === 'lines' && <AgentLinesView key="lines" />}
          {view === 'line-assembly' && <LineAssemblyView key="line-assembly" />}
        </Suspense>
      </div>
    </div>
  )
}
