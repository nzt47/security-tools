/**
 * NavPanel —— 统一工作台全功能导航（左侧边栏）
 * ------------------------------------------------
 * 渲染 HUB_NAV 导航树（9 大栏目 + 系统管理子菜单），
 * 点击叶子项 → 更新 workbench 导航状态 → ContentPanel 切换内容。
 * 分组项展开/收起；当前选中项高亮。
 *
 * 【2026-09-22 新增搜索框】导航树有 14 个顶层项、60+ 叶子项，而侧栏滚动条只有
 * 6px / 25% 透明度（`styles/workbench.css`）—— 实测操作者滚不到「治理面板」
 * 那一层，连带找不到它下面的「审批收件箱」，于是审批单据挂单后长时间无人裁决。
 * 搜索是这个问题的正面解：输入「审批」直达，无需先知道它在哪一栏下面。
 */
import { useMemo, useState } from 'react'
import { ChevronDown, Cloud, Search, X } from 'lucide-react'
import { HUB_NAV, filterNav, type HubNavItem } from '../../../workbench/hubNav'
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

function NavGroup({
  item,
  depth,
  forceOpen = false,
}: {
  item: HubNavItem
  depth: number
  /** 搜索态强制展开：命中项若藏在折叠的分组里，等于「搜了也看不见」 */
  forceOpen?: boolean
}) {
  const [open, setOpen] = useState(depth === 0)
  const Icon = item.icon
  const hasActiveChild = item.children?.some((c) => useWorkbenchNav.getState().activeKey === c.key)
  const expanded = forceOpen || open

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
        <ChevronDown size={12} className={`shrink-0 transition-transform ${expanded ? '' : '-rotate-90'}`} />
      </button>
      {expanded && (
        <div className="ml-2 mt-0.5 space-y-0.5 border-l border-slate-800 pl-1.5">
          {item.children?.map((child) =>
            child.children ? (
              <NavGroup key={child.key} item={child} depth={depth + 1} forceOpen={forceOpen} />
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
  const [query, setQuery] = useState('')
  const searching = query.trim().length > 0
  // HUB_NAV 是模块级常量（单一数据源）；只有 query 变化才需要重算过滤
  const tree = useMemo(() => filterNav(HUB_NAV, query), [query])

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

      {/* 搜索框（Esc 清空；命中分组时整组保留，命中子项时只留该子项） */}
      <div className="border-b border-slate-800 px-2 py-2">
        <div className="relative">
          <Search
            size={12}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-slate-500"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setQuery('')
            }}
            placeholder="搜索功能（如：审批）"
            aria-label="搜索功能"
            data-cp-nav-search
            className="w-full rounded-md border border-slate-800 bg-slate-950/60 py-1 pl-6 pr-6 text-[12px] text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-none"
          />
          {searching && (
            <button
              type="button"
              aria-label="清空搜索"
              data-cp-nav-search-clear
              onClick={() => setQuery('')}
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-500 hover:text-slate-200"
            >
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* 导航树 */}
      <div className="wb-think-scroll min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {tree.length === 0 ? (
          <div
            data-cp-nav-empty
            className="px-2 py-6 text-center text-[11.5px] leading-relaxed text-slate-500"
          >
            没有匹配「{query.trim()}」的功能
            <br />
            可试试「审批」「治理」「记忆」
          </div>
        ) : (
          tree.map((item) =>
            item.children ? (
              <NavGroup key={item.key} item={item} depth={0} forceOpen={searching} />
            ) : (
              <NavLeaf key={item.key} item={item} depth={0} />
            ),
          )
        )}
      </div>

      <div className="border-t border-slate-800 p-3 text-[9.5px] text-slate-600">
        云枢 · 统一工作台 v3
      </div>
    </div>
  )
}
