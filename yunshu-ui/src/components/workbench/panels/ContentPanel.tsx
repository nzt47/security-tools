/**
 * ContentPanel —— 统一工作台主内容区
 * ------------------------------------------------
 * 根据 workbench 导航状态（activeKey）从 HUB_NAV 映射渲染对应功能页。
 * 默认选中"会话任务"（对话流）；其他导航项渲染 Hub / 管理后台页面。
 * 页面组件为 React.lazy 懒加载（Code Splitting），Suspense 显示加载态。
 *
 * 复用组件参数化（缺陷 ②）：
 * - assets 组 8 个菜单共用 AssetsPage、memory/manual|auto 共用 MemoryPage，
 *   若只按组件引用渲染，点击不同菜单会渲染成相同内容。
 * - 修复：key={activeKey} 跨导航强制重挂载（清掉复用组件内部残留 state），
 *   并把 derivePanelParams(activeKey) 推导的"初始分类/模式"作为 props 传入，
 *   使复用页的初始视图跟随所选导航项。
 */
import { Suspense } from 'react'
import type { ComponentType } from 'react'
import { Loader2 } from 'lucide-react'
import { useWorkbenchNav } from '../../../workbench/navStore'
import { derivePanelParams, findNavItem, type HubPanelParams } from '../../../workbench/hubNav'

export function ContentPanel() {
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const item = findNavItem(activeKey)
  // hubNav 导航配置把组件声明为无 props 的 ComponentType；复用组件实际可接收
  // HubPanelParams（AssetsPage.initialCategory / MemoryPage.mode），此处收窄类型
  // 以便透传参数（其余页面组件忽略多余 props，行为不变）。
  const Component = (item?.component ?? null) as ComponentType<HubPanelParams> | null
  const params = derivePanelParams(activeKey)

  return (
    <div className="h-full min-h-0 overflow-y-auto bg-slate-950">
      {Component ? (
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center gap-2 text-slate-500">
              <Loader2 size={16} className="animate-spin" />
              <span className="text-sm">加载中…</span>
            </div>
          }
        >
          <Component key={activeKey} {...params} />
        </Suspense>
      ) : (
        <div className="flex h-full items-center justify-center text-sm text-slate-500">
          请从左侧导航选择功能
        </div>
      )}
    </div>
  )
}
