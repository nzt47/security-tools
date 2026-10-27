/**
 * 工具与接入（2026-10-28 由「工具调用」改名）—— 主内容区 Tab 容器：
 * 工具集 / 插件与扩展 / MCP 系统 / CLI 软件 / Computer Use / 工具治理
 * ------------------------------------------------------------------
 * 导航结构调整（与「提示词实验室」同款处理）：原「工具调用」是左侧导航树的**分组**，
 * 5 个子项各自占一条导航项，层级深且容易迷路。现收敛为：
 *   - 左侧导航：单一叶子项「工具调用」；
 *   - 主内容区顶部：一条 Tab 条并列子模块（子项即 Tab，点击切换、无需回导航树）。
 *
 * 【2026-10-08：第 5 个 Tab「主线管理」已迁出】它并入「装配车间 → 分身创建与组装」
 * （`pages/hub/workshop/agent-lines.tsx`）—— 理由：分身真正拿到什么由**主线档案**决定，
 * 与"创建分身"同屏才解释得通；且本仓禁止同一页面对外开两个入口。
 * 故本容器从 5 个 Tab 收缩为 4 个；Tab 记忆键沿用，历史值 `'lines'` 回落「工具集」。
 *
 * 实现要点：
 *   - 子模块仍保留**懒加载**（React.lazy），首屏只为当前 Tab 拉取 chunk；
 *   - Tab 选择记忆在 localStorage（重开页面回到上次所在子模块）；
 *   - 每个子模块自带 PageHeader，Tab 条只负责"并列 + 切换"，不改动子模块内部实现。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Boxes, Loader2, Monitor, Plug, Puzzle, Tags, Terminal, Wrench } from 'lucide-react'

const ToolsToolset = lazy(() => import('./toolset'))
const ToolsCli = lazy(() => import('./cli'))
const ToolsMcp = lazy(() => import('./mcp'))
const ToolsComputerUse = lazy(() => import('./computer-use'))
// 【2026-10-28 IA · 方案 A】插件与扩展并入本页（原 hubNav 的 components/plugins 叶子）：
// 插件包（manifest + 自带界面）是「能力从哪接进来」的另一类宿主，与 MCP/CLI/Computer Use 同族。
const ToolsPlugins = lazy(() => import('@/pages/hub/plugins'))
const ToolsGovernance = lazy(() => import('./governance'))

/** 子模块 Tab 键（= 原导航 key 的尾段，便于对照历史记录） */
export type ToolsTab = 'toolset' | 'plugins' | 'mcp' | 'cli' | 'computer-use' | 'governance'

export interface ToolsTabDef {
  id: ToolsTab
  label: string
  icon: LucideIcon
  hint: string
  component: ComponentType
}

/** 6 个子项（可调用面在前，接入/宿主随后：工具集 → 插件与扩展 → MCP → CLI → Computer Use → 治理） */
export const TOOLS_TABS: ToolsTabDef[] = [
  { id: 'toolset', label: '工具集', icon: Boxes, hint: '按四能力平面分组的工具配置 / 启停（含「可被 LLM 调用」标注）', component: ToolsToolset },
  // 【2026-10-28 IA · 方案 A】接入类 Tab 聚在一起：插件包 → MCP → CLI → Computer Use。
  { id: 'plugins', label: '插件与扩展', icon: Puzzle, hint: '插件包清单 / schema 配置 / 插件自带界面（/api/plugins）', component: ToolsPlugins },
  { id: 'mcp', label: 'MCP 系统', icon: Plug, hint: 'MCP 服务连接与外部能力接入', component: ToolsMcp },
  { id: 'cli', label: 'CLI 软件', icon: Terminal, hint: '命令行工具与系统进程管理', component: ToolsCli },
  { id: 'computer-use', label: 'Computer Use', icon: Monitor, hint: '浏览器自动化与屏幕操作', component: ToolsComputerUse },
  { id: 'governance', label: '工具治理', icon: Tags, hint: '分类 / 健康 / 路由关键词 / 启用总览（关键词决定哪类工具被选中）', component: ToolsGovernance },
]

/** Tab 选择记忆（会话级偏好，刷新不跳回第一个 Tab） */
export const TOOLS_TAB_STORAGE_KEY = 'yunshu.tools.tab'

export const isToolsTab = (v: unknown): v is ToolsTab =>
  typeof v === 'string' && TOOLS_TABS.some((t) => t.id === v)

/**
 * 读 Tab 记忆；非法值回落「工具集」。
 *
 * 【为什么这里要显式说明】`'lines'`（原第 5 个 Tab 的 id）现在不在 `TOOLS_TABS` 里，
 * 于是老用户 localStorage 里的 `'lines'` 会**静默回落**到「工具集」—— 这不是缺陷，
 * 是"Tab 已迁走"的预期行为；回归用例见 `index.test.tsx`
 * （「Tab 迁走后，记忆里的旧值 'lines' 回落工具集」）。
 */
function readSavedTab(): ToolsTab {
  try {
    const v = localStorage.getItem(TOOLS_TAB_STORAGE_KEY)
    return isToolsTab(v) ? v : 'toolset'
  } catch {
    return 'toolset'
  }
}

export interface ToolsPageProps {
  /** 初始 Tab（缺省用 URL `?tab=`，再缺省用 localStorage 记忆；非法值回落「工具集」） */
  initialTab?: ToolsTab
}

/**
 * 从 URL 读 Tab（`?tab=cli` / hash 里的 `?tab=cli`）——深链入口。
 *
 * 【为什么补这个】以前 Tab 只记在 localStorage：想直接给某人「CLI 软件」的链接做不到，
 * 手册/工单里只能写"点工具调用再点第二个 Tab"。URL 与 localStorage 同时存在时 **URL 优先**
 * （分享出去的链接应当落在作者指的位置），非法值返回 null 让调用方回退。
 */
export function readUrlTab(rawSearch?: string): ToolsTab | null {
  if (typeof window === 'undefined' && rawSearch === undefined) return null
  const sources = rawSearch !== undefined
    ? [rawSearch]
    : [window.location.search.replace(/^\?/, ''), (window.location.hash.split('?')[1] ?? '')]
  for (const source of sources) {
    const value = new URLSearchParams(source).get('tab')
    if (isToolsTab(value)) return value
  }
  // 【2026-10-28】旧深链 ?panel=components/plugins（插件与扩展曾是独立叶子）落到本页时，
  // 要直接打开「插件与扩展」Tab —— 否则人看到的是「工具集」，会以为插件页没了。
  // ?tab= 优先（tab 是页内显式意图）；panel 只在没有 tab 时兜底。
  for (const source of sources) {
    const panel = new URLSearchParams(source).get('panel')
    if (panel === 'components/plugins' || panel === 'extensions') return 'plugins'
  }
  return null
}

/** 把当前 Tab 写回 URL（replaceState，不产生历史记录；尽量保留 hash 路由的形状） */
export function writeUrlTab(tab: ToolsTab): void {
  if (typeof window === 'undefined') return
  try {
    const hash = window.location.hash || ''
    if (hash.includes('?')) {
      const [path, query = ''] = hash.split('?')
      const params = new URLSearchParams(query)
      if (params.get('tab') === tab) return
      params.set('tab', tab)
      window.history.replaceState({}, '', path + '?' + params.toString())
      return
    }
    const url = new URL(window.location.href)
    if (url.searchParams.get('tab') === tab) return
    url.searchParams.set('tab', tab)
    window.history.replaceState({}, '', url.toString())
  } catch {
    /* 无 history / 非法 URL 时只保留内存态 */
  }
}

export default function ToolsPage({ initialTab }: ToolsPageProps) {
  const [tab, setTab] = useState<ToolsTab>(() => {
    if (isToolsTab(initialTab)) return initialTab
    return readUrlTab() ?? readSavedTab()
  })

  useEffect(() => {
    try {
      localStorage.setItem(TOOLS_TAB_STORAGE_KEY, tab)
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
    writeUrlTab(tab)
  }, [tab])

  const active = TOOLS_TABS.find((t) => t.id === tab) ?? TOOLS_TABS[0]
  const ActiveComponent = active.component

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 顶栏：模块标题 + Tab 条（6 个子项并列） */}
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <Wrench size={14} className="text-cyan-400" />
          <h1 className="text-[15px] font-semibold text-slate-100">工具与接入</h1>
          <span className="text-[11px] text-slate-500">{active.hint}</span>
        </div>

        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="工具与接入子模块">
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
