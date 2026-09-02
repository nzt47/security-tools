/**
 * ContentPanel —— 统一工作台主内容区
 * ------------------------------------------------
 * 根据 workbench 导航状态（activeKey）从 HUB_NAV 映射渲染对应功能页。
 * 默认选中"会话任务"（对话流）；其他导航项渲染 Hub / 管理后台页面。
 * 页面组件为 React.lazy 懒加载（Code Splitting），Suspense 显示加载态。
 */
import { Suspense } from 'react'
import { Loader2 } from 'lucide-react'
import { useWorkbenchNav } from '../../../workbench/navStore'
import { findNavItem } from '../../../workbench/hubNav'

export function ContentPanel() {
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const item = findNavItem(activeKey)
  const Component = item?.component ?? null

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
          <Component />
        </Suspense>
      ) : (
        <div className="flex h-full items-center justify-center text-sm text-slate-500">
          请从左侧导航选择功能
        </div>
      )}
    </div>
  )
}
