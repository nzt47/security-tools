/**
 * 记忆管理 —— 主内容区 Tab 容器（向量记忆 / 记忆结构 / 知识库系统 / 经验库 / 全库检索）
 * 【第四轮 IA · 2026-10-14】原「手动记忆」+「自动记忆」两个 Tab 合并为「向量记忆」
 * （页内分段控件保留两种视图），并新增「记忆结构」Tab。
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
import { BookOpen, Brain, Layers, Loader2, Search } from 'lucide-react'

// MemoryPage 同时承载「手动记忆 / 自动记忆」（由 mode 参数区分）
const MemoryPage = lazy(() => import('./index'))
const MemoryKnowledge = lazy(() => import('./knowledge'))
const ExperiencePanel = lazy(() => import('../experience'))
const MemorySearch = lazy(() => import('./search'))
const MemoryStructure = lazy(() => import('./structure'))

export type MemoryTab = 'vector' | 'structure' | 'knowledge' | 'experience' | 'search'

export interface MemoryTabDef {
  id: MemoryTab
  label: string
  icon: LucideIcon
  hint: string
}

/** 子模块（【第四轮 IA · 2026-10-14】手动记忆 + 自动记忆合并为「向量记忆」，
 *  并新增「记忆结构」；顺序 = 展示顺序） */
export const MEMORY_TABS: MemoryTabDef[] = [
  { id: 'vector', label: '向量记忆', icon: Brain, hint: '记忆本体：手动录入 + 对话中自动沉淀，统一走向量检索' },
  { id: 'structure', label: '记忆结构', icon: Layers, hint: '记忆的层次、组成部分、数据来源与职责边界' },
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
    if (!isMemoryTab(v)) {
      // 【第四轮 IA】旧值 manual / auto 已合并为 vector：沿用「上次看的是手动还是自动」
      // 的记忆时，统一落到合并后的「向量记忆」（否则会静默跳回第一个 Tab）。
      return v === 'manual' || v === 'auto' ? 'vector' : 'vector'
    }
    return v
  } catch {
    return 'vector'
  }
}

export interface MemoryHubProps {
  /** 初始 Tab（缺省用 localStorage 记忆的值；非法值回落第一个 Tab「向量记忆」） */
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
  // 合并后的「向量记忆」内部再分「手动录入 / 自动沉淀」两种视图（功能不减，入口合一）
  const [vectorMode, setVectorMode] = useState<'manual' | 'auto'>('manual')

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
          {tab === 'vector' ? (
            <div className="flex min-h-full flex-col">
              {/* 合并前的两个入口（手动记忆 / 自动记忆）→ 页内分段控件，避免功能丢失 */}
              <div className="flex items-center gap-2 border-b border-slate-800/60 bg-slate-900/30 px-5 py-2">
                <div className="flex overflow-hidden rounded-md border border-slate-700" role="tablist" aria-label="向量记忆视图">
                  {([['manual', '手动录入'], ['auto', '自动沉淀']] as const).map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={vectorMode === id}
                      onClick={() => setVectorMode(id)}
                      className={vectorMode === id
                        ? 'bg-cyan-500/15 px-3 py-1 text-[11.5px] font-medium text-cyan-300'
                        : 'px-3 py-1 text-[11.5px] text-slate-400 hover:bg-slate-800'}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <span className="text-[11px] text-slate-500">
                  {vectorMode === 'manual' ? '手动录入关键信息' : '对话过程中自动沉淀的记忆'}
                </span>
              </div>
              <MemoryPage mode={vectorMode} />
            </div>
          ) : tab === 'structure' ? (
            <MemoryStructure />
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
