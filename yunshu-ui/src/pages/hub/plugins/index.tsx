/**
 * 插件与扩展 —— 「会话与配置」桶下的 Tab 容器（UI IA 整合阶段 1.2；第四轮 IA 迁入本桶）
 * ------------------------------------------------------------------
 * 【为什么合并】原「扩展中心」（`features/extensions` 自动发现项）与「插件管理」
 * （`pages/hub/plugin-manage`）读同一份 `/api/plugins`，是两个同源入口（方案 P6）。
 * 现收敛为一个导航项「插件与扩展」，内部两 Tab：
 *   ① 清单与配置：逐插件展开 + Schema 表单 + 令牌/重载（原插件管理）；
 *   ② 扩展呈现：所有声明 schema / client_slot 的插件铺一屏，含插件自带 UI（原扩展中心）。
 *
 * 【保留的关键链路】`plugins/extensionHost` 的 `registerPluginModule` 仍是插件自带 UI 的
 * 唯一入口，由「扩展呈现」Tab 消费，未改其行为。
 *
 * 【与「工具调用」容器同款实现】子模块懒加载、Tab 记忆在 localStorage、每个子模块自带
 * PageHeader，Tab 条只负责「并列 + 切换」。两个 Tab 互斥显示，因此同屏不会出现两个同源
 * 刷新按钮（方案 §4.2「避免两个刷新按钮」由 Tab 结构本身达成；未把 /api/plugins 状态上提，
 * 以免为不可见的收益引入容器↔子页耦合）。
 *
 * 【禁止双入口】原 features/extensions 的 manifest 已删除，本页是插件的唯一导航入口。
 */
import { Suspense, lazy, useEffect, useState } from 'react'
import type { ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Boxes, Loader2, Puzzle, Settings2 } from 'lucide-react'

const PluginManage = lazy(() => import('../plugin-manage'))
const PluginExtensions = lazy(() => import('./extensions'))

/** 子模块 Tab 键 */
export type PluginsTab = 'manage' | 'extensions'

export interface PluginsTabDef {
  id: PluginsTab
  label: string
  icon: LucideIcon
  hint: string
  component: ComponentType
}

/** 两个子项（顺序 = 方案 §4.2：清单与配置 → 扩展呈现） */
export const PLUGINS_TABS: PluginsTabDef[] = [
  { id: 'manage', label: '清单与配置', icon: Settings2, hint: '逐插件展开：Schema 驱动配置表单、令牌与路由明细（原「插件管理」）', component: PluginManage },
  { id: 'extensions', label: '扩展呈现', icon: Boxes, hint: '所有声明 schema / client_slot 的插件自动铺开，含插件自带 UI（原「扩展中心」）', component: PluginExtensions },
]

/** Tab 选择记忆（会话级偏好，刷新不跳回第一个 Tab） */
export const PLUGINS_TAB_STORAGE_KEY = 'yunshu.plugins.tab'

export const isPluginsTab = (v: unknown): v is PluginsTab =>
  typeof v === 'string' && PLUGINS_TABS.some((t) => t.id === v)

function readSavedTab(): PluginsTab {
  try {
    const v = localStorage.getItem(PLUGINS_TAB_STORAGE_KEY)
    return isPluginsTab(v) ? v : 'manage'
  } catch {
    return 'manage'
  }
}

export interface PluginsPageProps {
  /** 初始 Tab（缺省用 localStorage 记忆的值；非法值回落「清单与配置」） */
  initialTab?: PluginsTab
}

export default function PluginsPage({ initialTab }: PluginsPageProps) {
  const [tab, setTab] = useState<PluginsTab>(() => (isPluginsTab(initialTab) ? initialTab : readSavedTab()))

  useEffect(() => {
    try {
      localStorage.setItem(PLUGINS_TAB_STORAGE_KEY, tab)
    } catch {
      /* localStorage 不可用时仅内存态 */
    }
  }, [tab])

  const active = PLUGINS_TABS.find((t) => t.id === tab) ?? PLUGINS_TABS[0]
  const ActiveComponent = active.component

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-5 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <Puzzle size={14} className="text-cyan-400" />
          <h1 className="text-[15px] font-semibold text-slate-100">插件与扩展</h1>
          <span className="text-[11px] text-slate-500">{active.hint}</span>
        </div>

        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="插件与扩展子模块">
          {PLUGINS_TABS.map((t) => {
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
