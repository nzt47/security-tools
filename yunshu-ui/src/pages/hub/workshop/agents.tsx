/**
 * 装配车间 —— 分身创建与组装（多 agent 设计系统）
 * ------------------------------------------------------------------
 * 【2026-10-08 组装台：本页成为「分身装配」的唯一入口】四个视图：
 *   ① 分身        —— 存活分身列表 / 销毁 + 生效徽章（`/api/subagent/*`）；
 *   ② 组装台      —— 六步拼装 + **主权清单**（后端三态投影）+ 装配预览（真实请求体），
 *                     取代原「创建分身」四格弹表单（**唯一创建入口**）；
 *   ③ 主线档案    —— 能力平面档案（权重/保底/效果上限/技能包/提示词）+ 实时装配预览；
 *   ④ 主线 × 四面 —— 一条主线在「工具 / 技能 / 提示词 / 分身」四个面上的横向对比。
 *
 * 【为什么把"创建"搬进组装台】原创建表单把"主权二十面"压成四个输入框，其中
 * `memory_provider` / `tool_sources` **没有任何运行时消费者**（只进状态与热更新日志）。
 * 组装台把每一面的真实状态（拥有 / 声明未接线 / 未做）由后端投影摆出来，并把
 * "装配预览 = 真实请求体"钉成同源 —— 表单承诺与运行时事实不再各说各话。
 * 权威设计见 `docs/主权分身_维度与组装页设计_20261008.md`。
 *
 * 【Tab 选择记忆】与「工具调用」Tab 容器同款：localStorage 记住上次视图，非法值回落
 * 「分身」。注意两个 key 是**分开的**（`yunshu.workshop.view` 与本页无关的
 * `yunshu.tools.tab`）：原「工具调用 → 主线管理」Tab 被摘掉后，那边记忆里残留的
 * `'lines'` 会在**那边**回落成「工具集」（由 `tools/index.test.tsx` 钉住），
 * 不会漂移到本页；本页视图 id 复用 `'lines'` 只是名字相同，两处互不影响。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Hammer, Layers, Loader2, Route, Trash2, Users } from 'lucide-react'
import { Card, Loading, ErrorBox, DataTable, Badge, PageHeader, hubPost } from '../components/ui'

import {
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

/** 视图键（= 原导航/Tab 身份，便于对照历史记录） */
export type WorkshopView = 'agents' | 'assembly' | 'lines' | 'line-assembly'

export interface WorkshopViewDef {
  id: WorkshopView
  label: string
  icon: LucideIcon
  hint: string
}

/** 四个视图：顺序 = 「先有分身 → 再装一个 → 再看它拿到什么 → 再看四个面」 */
export const WORKSHOP_VIEWS: WorkshopViewDef[] = [
  { id: 'agents', label: '分身', icon: Users, hint: '存活分身列表与销毁（生效模型 / 角色来源见表中徽章）' },
  { id: 'assembly', label: '组装台', icon: Hammer, hint: '六步拼装一个主权分身：主权清单（三态）+ 装配预览（真实请求体）' },
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

const AssemblyConsole = lazy(() => import('./assembly'))
const AgentLinesView = lazy(() => import('./agent-lines'))
const LineAssemblyView = lazy(() => import('./line-assembly'))

export interface WorkshopAgentsProps {
  /** 初始视图（缺省用 localStorage 记忆的值；非法值回落「分身」） */
  initialView?: WorkshopView
}

// ═══════════════════════════════════════════════════════════
//  ① 分身：创建 / 列表 / 销毁（本页原有内容，一字未改语义）
// ═══════════════════════════════════════════════════════════

function SubagentView({ onShowAssembly }: { onShowAssembly?: () => void }) {
  const [agents, setAgents] = useState<Subagent[]>([])
  const [deployment, setDeployment] = useState<DeploymentLlm | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // 角色模板词表：仅用于把 template id 显示成人读标题（生效情况由后端 role 段给）
  const [roleCatalog, setRoleCatalog] = useState<RoleCatalog | null>(null)

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

  const destroy = async (n: string) => {
    try {
      await hubPost(subagentDestroyByName(n))
      load()
    } catch (e) { setError(String(e)) }
  }

  const deploymentModel = String(deployment?.model || '')

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs text-slate-500">
          装配一个分身请到「组装台」（六步 + 主权清单 + 装配预览，唯一创建入口）。
          本页只列**存活分身**与生效来源：<span className="text-slate-400">模型已接线</span>
          （空 = 跟随母体{deploymentModel ? `：${deploymentModel}` : '（母体模型未知）'}，指定则派生独立实例），
          <span className="text-slate-400">角色已接线</span>
          （受控模板；自由文本须显式选 template+text / full-system 档）。
          记忆提供商 / 工具源仍是**声明字段**（尚未接线，见后续阶段）。
        </span>
        <button onClick={onShowAssembly} data-testid="go-assembly" className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"><Hammer size={12} /> 前往组装台</button>
      </div>
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
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
//  容器：四视图 Tab
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
      {/* 顶栏：模块标题 + 四视图 Tab 条（子视图即 Tab，点击切换、无需回导航树） */}
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
                title="分身列表"
                description="存活分身与生效来源（创建请到「组装台」）"
              />
              <SubagentView key="agents" onShowAssembly={() => setView('assembly')} />
            </div>
          )}
          {view === 'assembly' && <AssemblyConsole key="assembly" onShowAgents={() => setView('agents')} />}
          {view === 'lines' && <AgentLinesView key="lines" />}
          {view === 'line-assembly' && <LineAssemblyView key="line-assembly" />}
        </Suspense>
      </div>
    </div>
  )
}
