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
}

/** 模型来源徽章（三档与后端 `source` 一一对应；"未生效"必须是红档，不许静默） */
function LlmSourceBadge({ llm }: { llm?: SubagentLlm }) {
  if (!llm) return null
  const source = String(llm.source || '')
  if (source === 'explicit') return <Badge color="green">指定模型</Badge>
  if (source === 'inherit') return <Badge color="slate">跟随母体</Badge>
  return <Badge color="red">指定未生效</Badge>
}

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
  const [memory, setMemory] = useState('default')
  const [tools, setTools] = useState('')

  const load = () => {
    setLoading(true)
    getEnvelope<SubagentList>(SUBAGENT_LIST).then((d) => {
      setAgents(d?.subagents ?? [])
      setDeployment(d?.llm ?? null)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const create = async () => {
    try {
      await hubPost(SUBAGENT_CREATE, {
        name,
        model_id: model.trim(),  // 空串 = 跟随母体（后端按 inherit 档解析）
        memory_provider: memory,
        tool_sources: tools ? tools.split(',').map((t) => t.trim()).filter(Boolean) : [],
        tags: ['hub'],
      })
      setShowForm(false)
      setName(''); setTools('')
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

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs text-slate-500">
          工具集由「主线档案」决定（装配时去 govern、去 §5.7 机制 3 硬禁）；
          <span className="text-slate-400">模型已接线</span>
          （留空 = 跟随母体{deploymentModel ? `：${deploymentModel}` : '（母体模型未知）'}，
          指定则派生独立实例；生效来源见表中徽章）。
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
                  return (
                    <span className="flex flex-wrap items-center gap-1.5" data-testid={`subagent-llm-${String(r.name)}`}>
                      <span className="font-mono text-xs text-cyan-400" data-testid="subagent-llm-model">
                        {effective || '未解析'}
                      </span>
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
