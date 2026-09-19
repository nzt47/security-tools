/**
 * 工具调用 —— 主内容区 Tab 容器（工具集 / CLI 软件 / MCP 系统 / Computer Use / 主线管理）
 * ------------------------------------------------------------------
 * 导航结构调整（与「提示词实验室」同款处理）：原「工具调用」是左侧导航树的**分组**，
 * 5 个子项各自占一条导航项，层级深且容易迷路。现收敛为：
 *   - 左侧导航：单一叶子项「工具调用」；
 *   - 主内容区顶部：一条 Tab 条并列 5 个子模块（子项即 Tab，点击切换、无需回导航树）。
 *
 * 实现要点：
 *   - 5 个子模块仍保留**懒加载**（React.lazy），首屏只为当前 Tab 拉取 chunk；
 *   - Tab 选择记忆在 localStorage（重开页面回到上次所在子模块）；
 *   - 每个子模块自带 PageHeader，Tab 条只负责"并列 + 切换"，不改动子模块内部实现。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Boxes, Loader2, Monitor, Plug, Route, Terminal, Wrench } from 'lucide-react'

const ToolsToolset = lazy(() => import('./toolset'))
const ToolsCli = lazy(() => import('./cli'))
const ToolsMcp = lazy(() => import('./mcp'))
const ToolsComputerUse = lazy(() => import('./computer-use'))
const ToolsAgentLines = lazy(() => import('./lines'))

/** 子模块 Tab 键（= 原导航 key 的尾段，便于对照历史记录） */
export type ToolsTab = 'toolset' | 'cli' | 'mcp' | 'computer-use' | 'lines'

export interface ToolsTabDef {
  id: ToolsTab
  label: string
  icon: LucideIcon
  hint: string
  component: ComponentType
}

/** 5 个子项（顺序 = 导航原顺序：工具集 → CLI → MCP → Computer Use → 主线管理） */
export const TOOLS_TABS: ToolsTabDef[] = [
  { id: 'toolset', label: '工具集', icon: Boxes, hint: '按四能力平面分组的工具配置 / 启停（含「可被 LLM 调用」标注）', component: ToolsToolset },
  { id: 'cli', label: 'CLI 软件', icon: Terminal, hint: '命令行工具与系统进程管理', component: ToolsCli },
  { id: 'mcp', label: 'MCP 系统', icon: Plug, hint: 'MCP 服务连接与外部能力接入', component: ToolsMcp },
  { id: 'computer-use', label: 'Computer Use', icon: Monitor, hint: '浏览器自动化与屏幕操作', component: ToolsComputerUse },
  { id: 'lines', label: '主线管理', icon: Route, hint: '能力平面档案（权重 / 核心工具 / 效果上限）与装配预览', component: ToolsAgentLines },
]

/** Tab 选择记忆（会话级偏好，刷新不跳回第一个 Tab） */
export const TOOLS_TAB_STORAGE_KEY = 'yunshu.tools.tab'

export const isToolsTab = (v: unknown): v is ToolsTab =>
  typeof v === 'string' && TOOLS_TABS.some((t) => t.id === v)

function readSavedTab(): ToolsTab {
  try {
    const v = localStorage.getItem(TOOLS_TAB_STORAGE_KEY)
    return isToolsTab(v) ? v : 'toolset'
  } catch {
    return 'toolset'
  }
}

export interface ToolsPageProps {
  /** 初始 Tab（缺省用 localStorage 记忆的值；非法值回落「工具集」） */
  initialTab?: ToolsTab
}

export default function ToolsPage({ initialTab }: ToolsPageProps) {
  const [tab, setTab] = useState<ToolsTab>(() => (isToolsTab(initialTab) ? initialTab : readSavedTab()))

  useEffect(() => {
    try {
      localStorage.setItem(TOOLS_TAB_STORAGE_KEY, tab)
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [tab])

  const active = TOOLS_TABS.find((t) => t.id === tab) ?? TOOLS_TABS[0]
  const ActiveComponent = active.component

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶栏：模块标题 + Tab 条（5 个子项并列） */}
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <Wrench size={14} className="text-cyan-400" />
          <h1 className="text-[15px] font-semibold text-slate-100">工具调用</h1>
          <span className="text-[11px] text-slate-500">{active.hint}</span>
        </div>

        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="工具调用子模块">
          {TOOLS_TABS.map((t) => {
            const Icon = t.icon
            const selected = tab === t.id
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
                onClick={() => setTab(t.id)}
                title={t.hint}
              >
                <Icon size={13} className={selected ? 'text-cyan-400' : 'text-slate-500'} />
                {t.label}
              </button>
            )
          })}
        </nav>
      </header>

      {/* Tab 内容：key={tab} 强制重挂载，避免切换后残留上一个子模块的局部状态 */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-slate-950">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center gap-2 text-slate-500">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">加载中…</span>
            </div>
          }
        >
          <ActiveComponent key={tab} />
        </Suspense>
      </div>
    </div>
  )
}
