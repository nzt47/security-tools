/**
 * 记忆管理 —— 主内容区 Tab 容器（手动记忆 / 自动记忆 / 知识库系统 / 经验库 / 全库检索）
 * ------------------------------------------------------------------
 * 信息架构调整（与「工具调用」「提示词实验室」同款处理）：原「记忆管理」是左侧导航树的
 * **分组**，5 个子项各占一条导航项。现收敛为：
 *   - 左侧导航：单一叶子项「记忆管理」（排在「工具调用」之后）；
 *   - 主内容区顶部：一条 Tab 条并列子模块（子项即 Tab，点击切换、无需回导航树）。
 *
 * 实现要点与 pages/hub/tools/index.tsx 一致：
 *   - 子模块保留**懒加载**（React.lazy），首屏只为当前 Tab 拉取 chunk；
 *   - Tab 选择记忆在 localStorage（重开页面回到上次所在子模块）；
 *   - 每个子模块自带 PageHeader，Tab 条只负责"并列 + 切换"，不改动子模块内部实现。
 *
 * 旧深链 ?panel=memory/manual|auto|knowledge|experience|search 经 hubNav 的
 * LEGACY_NAV_KEY_MAP 归一落到本页（默认 Tab，Tab 记忆会接续上次选择）。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { BookOpen, Brain, Loader2, RefreshCw, Search } from 'lucide-react'

// MemoryPage 同时承载「手动记忆 / 自动记忆」（由 mode 参数区分）
const MemoryPage = lazy(() => import('./index'))
const MemoryKnowledge = lazy(() => import('./knowledge'))
const ExperiencePanel = lazy(() => import('../experience'))
const MemorySearch = lazy(() => import('./search'))

export type MemoryTab = 'manual' | 'auto' | 'knowledge' | 'experience' | 'search'

export interface MemoryTabDef {
  id: MemoryTab
  label: string
  icon: LucideIcon
  hint: string
}

/** 5 个子项（顺序 = 原导航顺序） */
export const MEMORY_TABS: MemoryTabDef[] = [
  { id: 'manual', label: '手动记忆', icon: Brain, hint: '手动录入关键信息 + 知识检索' },
  { id: 'auto', label: '自动记忆', icon: RefreshCw, hint: '对话过程中自动沉淀的记忆 / 知识检索' },
  { id: 'knowledge', label: '知识库系统', icon: BookOpen, hint: '知识卡片编辑 / 双链图谱 / 融合检索' },
  { id: 'experience', label: '经验库', icon: BookOpen, hint: '从历史会话提炼的任务经验（审查台）' },
  { id: 'search', label: '全库检索', icon: Search, hint: '一个入口，分面检索：向量记忆 / 知识库 / 经验库' },
]

/** Tab 选择记忆（会话级偏好，刷新不跳回第一个 Tab） */
export const MEMORY_TAB_STORAGE_KEY = 'yunshu.memory.tab'

export const isMemoryTab = (v: unknown): v is MemoryTab =>
  typeof v === 'string' && MEMORY_TABS.some((t) => t.id === v)

function readSavedTab(): MemoryTab {
  try {
    const v = localStorage.getItem(MEMORY_TAB_STORAGE_KEY)
    return isMemoryTab(v) ? v : 'manual'
  } catch {
    return 'manual'
  }
}

export interface MemoryHubProps {
  /** 初始 Tab（缺省用 localStorage 记忆的值；非法值回落「手动记忆」） */
  initialTab?: MemoryTab
}

function tabClass(selected: boolean): string {
  const base = 'flex items-center gap-1.5 rounded-t-lg border border-b-0 px-3 py-1.5 text-[12.5px] transition-colors '
  return base + (selected
    ? 'border-slate-700 bg-slate-950 font-medium text-cyan-300'
    : 'border-transparent text-slate-400 hover:bg-slate-800/60 hover:text-slate-200')
}

export default function MemoryHub({ initialTab }: MemoryHubProps) {
  const [tab, setTab] = useState<MemoryTab>(() => (isMemoryTab(initialTab) ? initialTab : readSavedTab()))

  useEffect(() => {
    try {
      localStorage.setItem(MEMORY_TAB_STORAGE_KEY, tab)
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [tab])

  const active = MEMORY_TABS.find((t) => t.id === tab) ?? MEMORY_TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <Brain size={14} className="text-cyan-400" />
          <h1 className="text-[15px] font-semibold text-slate-100">记忆管理</h1>
          <span className="text-[11px] text-slate-500">{active.hint}</span>
        </div>

        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="记忆管理子模块">
          {MEMORY_TABS.map((t) => {
            const Icon = t.icon
            const selected = tab === t.id
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={selected}
                className={tabClass(selected)}
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

      <div className="min-h-0 flex-1 overflow-y-auto bg-slate-950">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center gap-2 text-slate-500">
              <Loader2 size={16} className="animate-spin" /> 载入中…
            </div>
          }
        >
          {tab === 'manual' || tab === 'auto' ? (
            <MemoryPage mode={tab} />
          ) : tab === 'knowledge' ? (
            <MemoryKnowledge />
          ) : tab === 'experience' ? (
            <ExperiencePanel />
          ) : (
            <MemorySearch />
          )}
        </Suspense>
      </div>
    </div>
  )
}
