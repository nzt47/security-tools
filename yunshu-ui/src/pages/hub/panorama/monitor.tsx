/**
 * 系统监控 —— 全景看板下「运行时视角」的**统一入口**（2026-10-14 合并「模块列表」）
 * ------------------------------------------------------------------
 * 【为什么合并】本页与「模块列表」原本都读 /api/modules/topology（前端**仅这两个消费方**），
 * 是同一份数据的两个入口 —— 正是本仓纪律要避免的「双入口」。合并后：
 *   · 总览     —— 运行状态 + 健康评分（原「系统监控」，见 monitor-overview.tsx）
 *   · 模块明细 —— 按域分组 + 搜索/筛选 + 危险等级 + 路径（原「模块列表」，module-list.tsx）
 *
 * 独立的 components/modules 叶子已从导航树移除；旧深链
 * ?panel=components/modules 经 hubNav 的 LEGACY_NAV_KEY_MAP 落到本页。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Activity, Loader2, ListTree } from 'lucide-react'

const MonitorOverview = lazy(() => import('./monitor-overview'))
const ModuleListPage = lazy(() => import('../module-list'))

export type MonitorTab = 'overview' | 'modules'

interface MonitorTabDef {
  id: MonitorTab
  label: string
  icon: LucideIcon
  hint: string
}

export const MONITOR_TABS: MonitorTabDef[] = [
  { id: 'overview', label: '总览', icon: Activity, hint: '模块运行状态与健康评分' },
  { id: 'modules', label: '模块明细', icon: ListTree, hint: '按域列出全部模块（状态/类型/路径/危险等级），支持搜索与筛选' },
]

const STORAGE_KEY = 'yunshu.panorama.monitor.tab'

function readSavedTab(): MonitorTab {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'modules' ? 'modules' : 'overview'
  } catch {
    return 'overview'
  }
}

function tabClass(selected: boolean): string {
  const base = 'flex items-center gap-1.5 rounded-t-lg border border-b-0 px-3 py-1.5 text-[12.5px] transition-colors '
  return base + (selected
    ? 'border-slate-700 bg-slate-950 font-medium text-cyan-300'
    : 'border-transparent text-slate-400 hover:bg-slate-800/60 hover:text-slate-200')
}

export default function PanoramaMonitor() {
  const [tab, setTab] = useState<MonitorTab>(readSavedTab)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, tab)
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [tab])

  const active = MONITOR_TABS.find((t) => t.id === tab) ?? MONITOR_TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <Activity size={14} className="text-cyan-400" />
          <h1 className="text-[15px] font-semibold text-slate-100">系统监控</h1>
          <span className="text-[11px] text-slate-500">{active.hint}</span>
        </div>
        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="系统监控子视图">
          {MONITOR_TABS.map((t) => {
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
          {tab === 'overview' ? <MonitorOverview /> : <ModuleListPage />}
        </Suspense>
      </div>
    </div>
  )
}
