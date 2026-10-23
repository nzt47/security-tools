/**
 * 工作流 —— 独立叶子（2026-10-24 IA 调整）
 * ------------------------------------------------------------------
 * 为什么从「技能中心」拆出来：
 *   · LLM 技能 = 提示/行为注入、模型执行、按 token 计费；工作流 = 本地确定性 DAG、命中即 skip LLM。
 *     两者的管理对象（版本/评审/发布 vs 步骤/触发/执行统计）本来就不同，原先靠页内 Tab 强行同居；
 *   · 工作流原先藏在技能中心第 2 个 Tab，且 Tab 是组件内 state —— `?panel=skills-center` 永远落在 LLM，
 *     无法深链/刷新保持。拆成叶子后 `?panel=workflow` 天然可寻址，页内 Tab 也写进 `&tab=`。
 *   · 可视化编排的产物就是工作流，与「工作流技能」同域；技能中心则回归「单一 LLM 表」契约。
 */
import { useEffect, useState } from 'react'
import { PenTool, Workflow } from 'lucide-react'
import { MemoryWorkflowTable } from './workflow'
import MemoryWorkflowVisual from './workflow-visual'
import ConvertibleWorkflows from './ConvertibleWorkflows'

type Tab = 'skills' | 'visual'

const TABS: { key: Tab; label: string; icon: typeof Workflow; desc: string }[] = [
  { key: 'skills', label: '工作流技能', icon: Workflow, desc: '历史交互学习/编排出的确定性步骤 · 命中即本地执行（skipped_llm）' },
  { key: 'visual', label: '可视化编辑', icon: PenTool, desc: '拖拽编排工作流（保存到 /api/visual-workflows）' },
]

/** 当前 hash 里的 search 段（形如 '?panel=workflow&tab=visual'） */
function hashSearch(): string {
  const raw = window.location.hash || ''
  const i = raw.indexOf('?')
  return i >= 0 ? raw.slice(i) : ''
}

/** 从 URL 读 Tab（未知值回落 skills，保证深链/刷新一致） */
export function tabFromSearch(search: string): Tab {
  try {
    return new URLSearchParams(search).get('tab') === 'visual' ? 'visual' : 'skills'
  } catch {
    return 'skills'
  }
}

/** 把 Tab 写回 hash（保留 panel 等其它参数；skills 是默认值，不写进 URL） */
function writeTab(tab: Tab): void {
  const raw = window.location.hash || ''
  const base = raw.split('?')[0] || '#/workbench'
  const params = new URLSearchParams(hashSearch())
  if (tab === 'skills') params.delete('tab')
  else params.set('tab', tab)
  const q = params.toString()
  const target = base + (q ? `?${q}` : '')
  if (raw !== target) window.history.replaceState(null, '', target)
}

export default function WorkflowHub() {
  const [tab, setTab] = useState<Tab>(() => tabFromSearch(hashSearch()))
  const active = TABS.find((t) => t.key === tab)!

  // 手改地址栏 / 前进后退：只同步「URL → state」，写 URL 由 selectTab 负责，避免回环
  useEffect(() => {
    const apply = () => setTab(tabFromSearch(hashSearch()))
    window.addEventListener('hashchange', apply)
    return () => window.removeEventListener('hashchange', apply)
  }, [])

  const selectTab = (next: Tab) => {
    setTab(next)
    writeTab(next)
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <div className="border-b border-slate-800 px-5 pb-3 pt-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-700 bg-slate-900">
            <Workflow size={14} className="text-emerald-400" />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold text-slate-100">工作流</h1>
            <p className="text-[11px] text-slate-500">
              学习/编排得到的确定性步骤 —— 命中任务描述即本地执行并跳过 LLM；可视化编辑是它的画布入口
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1.5 border-b border-slate-800 px-5 py-2">
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => selectTab(t.key)}
              aria-selected={tab === t.key}
              title={t.desc}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ${
                tab === t.key
                  ? 'bg-emerald-500/15 font-medium text-emerald-300'
                  : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              }`}
            >
              <Icon size={13} />
              {t.label}
            </button>
          )
        })}
        <span className="ml-auto hidden font-mono text-[10px] text-slate-600 md:inline">{active.desc}</span>
      </div>

      <div className="min-h-0 flex-1">
        {tab === 'skills' && (
          <div className="h-full overflow-y-auto p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3.5 py-2.5">
              <div className="flex items-start gap-2 text-xs text-slate-400">
                <Workflow size={13} className="mt-0.5 shrink-0 text-emerald-400" />
                <span>
                  工作流技能由历史交互自动学习（或经可视化编排），命中任务描述即
                  <strong className="text-slate-200"> 本地执行并跳过 LLM</strong>（skipped_llm）。
                  列表已给出「说明 / 步骤链 / 类型 / 执行统计」，点<strong className="text-slate-200">「查看」</strong>可看完整说明、触发词与每步的工具与参数。
                </span>
              </div>
              <button
                type="button"
                onClick={() => selectTab('visual')}
                className="flex items-center gap-1.5 rounded-md bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-cyan-500"
              >
                <PenTool size={12} /> 可视化编辑 / 新建
              </button>
            </div>
            <MemoryWorkflowTable />
            {/* 可转换工作流（GET /api/workflow-learning/convertible）：属于工作流域，跟表格同页 */}
            <ConvertibleWorkflows />
          </div>
        )}

        {tab === 'visual' && (
          <div className="h-full min-h-0 overflow-hidden">
            <MemoryWorkflowVisual />
          </div>
        )}
      </div>
    </div>
  )
}
