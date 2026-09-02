/**
 * NavPanel —— 统一工作台全功能导航（左侧边栏）
 * ------------------------------------------------
 * 渲染 HUB_NAV 导航树（9 大栏目 + 系统管理子菜单），
 * 点击叶子项 → 更新 workbench 导航状态 → ContentPanel 切换内容。
 * 分组项展开/收起；当前选中项高亮。
 */
import { useState } from 'react'
import { ChevronDown, Cloud } from 'lucide-react'
import { HUB_NAV, type HubNavItem } from '../../../workbench/hubNav'
import { useWorkbenchNav } from '../../../workbench/navStore'

function NavLeaf({ item, depth }: { item: HubNavItem; depth: number }) {
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const setActiveKey = useWorkbenchNav((s) => s.setActiveKey)
  const Icon = item.icon
  const active = activeKey === item.key

  return (
    <button
      type="button"
      onClick={() => setActiveKey(item.key)}
      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12.5px] transition-colors ${
        active
          ? 'bg-cyan-500/15 font-medium text-cyan-300'
          : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
      }`}
      style={{ paddingLeft: `${depth * 12 + 10}px` }}
    >
      {Icon && <Icon size={13} className={active ? 'text-cyan-400' : 'text-slate-500'} />}
      <span className="truncate">{item.label}</span>
    </button>
  )
}

function NavGroup({ item, depth }: { item: HubNavItem; depth: number }) {
  const [open, setOpen] = useState(depth === 0)
  const Icon = item.icon
  const hasActiveChild = item.children?.some((c) => useWorkbenchNav.getState().activeKey === c.key)

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12.5px] font-medium transition-colors ${
          hasActiveChild ? 'text-cyan-300' : 'text-slate-300 hover:bg-slate-800/60'
        }`}
      >
        {Icon && <Icon size={13} className={hasActiveChild ? 'text-cyan-400' : 'text-slate-500'} />}
        <span className="flex-1 truncate">{item.label}</span>
        <ChevronDown size={12} className={`shrink-0 transition-transform ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && (
        <div className="ml-2 mt-0.5 space-y-0.5 border-l border-slate-800 pl-1.5">
          {item.children?.map((child) =>
            child.children ? (
              <NavGroup key={child.key} item={child} depth={depth + 1} />
            ) : (
              <NavLeaf key={child.key} item={child} depth={depth + 1} />
            ),
          )}
        </div>
      )}
    </div>
  )
}

export function NavPanel() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-900/80">
      {/* 品牌区 */}
      <div className="flex items-center gap-2 border-b border-slate-800 px-4 pb-3 pt-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600">
          <Cloud size={15} className="text-white" />
        </div>
        <div>
          <div className="text-[13px] font-semibold text-white">云枢工作台</div>
          <div className="font-mono text-[9px] uppercase tracking-[0.22em] text-slate-500">
            Unified Hub
          </div>
        </div>
      </div>

      {/* 导航树 */}
      <div className="wb-think-scroll min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {HUB_NAV.map((item) =>
          item.children ? (
            <NavGroup key={item.key} item={item} depth={0} />
          ) : (
            <NavLeaf key={item.key} item={item} depth={0} />
          ),
        )}
      </div>

      <div className="border-t border-slate-800 p-3 text-[9.5px] text-slate-600">
        云枢 · 统一工作台 v3
      </div>
    </div>
  )
}
