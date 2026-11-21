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
import { ChevronDown, Search, X } from 'lucide-react'
import { Button, Input, RowButton } from '@/components/ui'
import { HUB_NAV, filterNav, type HubNavItem } from '../../../workbench/hubNav'
import { useWorkbenchNav } from '../../../workbench/navStore'
import BuildStamp from '../BuildStamp'

function NavLeaf({ item, depth }: { item: HubNavItem; depth: number }) {
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const setActiveKey = useWorkbenchNav((s) => s.setActiveKey)
  const Icon = item.icon
  const active = activeKey === item.key

  return (
    <RowButton
      selected={active}
      onClick={() => setActiveKey(item.key)}
      className="px-2 py-1.5"
      style={{ paddingLeft: `${depth * 12 + 8}px` }}
      leading={Icon ? <Icon size={13} /> : undefined}
    >
      <span className="truncate">{item.label}</span>
    </RowButton>
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
      {/* 【2026-11-19】图标 / 文字 / 展开箭头必须走 RowButton 的 leading / trailing 槽：
          直接把三个子节点交给 children，会被 RowButton 包进**同一个** flex-1 span 里，
          而那个 span 不是 flex 容器 ⇒ 图标、文字、箭头各占一行（Owner 实测：分组标题变成三行）。 */}
      <RowButton
        selected={hasActiveChild}
        onClick={() => setOpen(!open)}
        className="px-2 py-1.5 font-medium"
        leading={Icon ? <Icon size={13} /> : undefined}
        trailing={<ChevronDown size={12} className={`transition-transform ${expanded ? '' : '-rotate-90'}`} />}
      >
        <span className="truncate">{item.label}</span>
      </RowButton>
      {expanded && (
        <div className="ml-2 mt-0.5 space-y-0.5 border-l border-border pl-1.5">
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
      {/* 2026-10-30：导航栏品牌区（logo + 「云枢工作台」+ Unified Hub）按 Owner 要求移除，
          顶部空间让给搜索框；品牌识别由浏览器标签页图标（/static/favicon.svg）承担。 */}

      {/* 搜索框（Esc 清空；命中分组时整组保留，命中子项时只留该子项） */}
      {/* 细条高度由 .wb-strip（--wb-strip-h 44px）统一给，别再写 py-* */}
      <div className="wb-strip border-b border-slate-800 px-1.5">
        {/* 2026-11-17：图标与清空按钮搬进 ui/Input 的 leading / trailing 槽（正文让位由原语给），
            data-cp-nav-search / -clear 两个测试钩子原样透传。 */}
        <Input
          type="text"
          size="sm"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setQuery('')
          }}
          placeholder="搜索功能（如：审批）"
          aria-label="搜索功能"
          data-cp-nav-search
          leading={<Search size={12} className="text-slate-500" />}
          trailing={
            searching ? (
              <Button type="button" aria-label="清空搜索" data-cp-nav-search-clear onClick={() => setQuery('')} variant="ghost" size="icon-xs">
                <X size={12} />
              </Button>
            ) : undefined
          }
        />
      </div>

      {/* 导航树 */}
      {/* 列表左右内缩交给行自己（px-2 = 8px，与右坞内容同档）：容器不再额外加 p-2，
          否则顶层行离面板边 8+8=16px，和右坞的 8px 不在一条线上。 */}
      <div className="wb-think-scroll min-h-0 flex-1 space-y-0.5 overflow-y-auto py-1">
        {tree.length === 0 ? (
          <div
            data-cp-nav-empty
            className="px-2 py-6 text-center text-body leading-relaxed text-slate-500"
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

      {/* 页脚：应用身份 + 构建戳（2026-11-21 从顶栏移到此处 —— 顶栏的构建戳与「当前叶子」
          都不是导航/操作，放这里与「云枢 · 统一工作台 v3」同处更自然）。 */}
      <div className="flex flex-col gap-0.5 border-t border-border p-3 text-micro text-muted-foreground">
        <span>云枢 · 统一工作台 v3</span>
        <BuildStamp />
      </div>
    </div>
  )
}
