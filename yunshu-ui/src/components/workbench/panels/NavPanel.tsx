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

/** 这棵子树里有没有 key 这一项（桶 → 小节 → 叶子的三级都要算） */
function containsKey(items: HubNavItem[] | undefined, key: string): boolean {
  return (items ?? []).some((c) => c.key === key || containsKey(c.children, key))
}

function NavGroup({
  item,
  depth,
}: {
  item: HubNavItem
  depth: number
}) {
  // 分级树：桶(0) → 小节(1) → 叶子(2)。
  // depth <= 1 让桶与小节都默认可见（旧逻辑只开 depth === 0）。
  // 【2026-11-22】观测桶的「运行 / 治理」小节已按 Owner 要求取消，本组件因此只剩
  // 桶 → 叶子两级；depth <= 1 仍然保留 —— 其余桶若日后加小节，不必再改这里。
  const [open, setOpen] = useState(depth <= 1)
  const Icon = item.icon
  // 【2026-11-21 修两个 bug】
  // ① 原来用 useWorkbenchNav.getState() 在渲染期读一次 ⇒ 组件**不订阅** store：
  //    深链进来时首次渲染 activeKey 还是默认值（session），算出的 hasActiveChild=true 就再也不会更新
  //    ⇒ 无论点哪个页面，「会话与能力」桶永远高亮（Owner 实报）。
  // ② 原来只比直接子项的 key ⇒ 观测桶（桶 → 小节 → 叶子）永远不会亮。
  // 改成订阅 activeKey + 递归判「这棵子树里有当前叶子」。
  // 【2026-11-22 删 forceOpen】旧写法 `expanded = forceOpen || open` 在搜索态把折叠钉死
  // （用户点标题没反应，与开关中心同一个病）。现在搜索态改用「查询变化时换 key 重挂」
  // 让分组回到默认展开（命中项照样可见），折叠权始终归用户。见 NavPanel 里的 treeKey。
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const hasActiveChild = containsKey(item.children, activeKey)
  const expanded = open

  return (
    <div>
      {/* 【2026-11-19】图标 / 文字 / 展开箭头必须走 RowButton 的 leading / trailing 槽：
          直接把三个子节点交给 children，会被 RowButton 包进**同一个** flex-1 span 里，
          而那个 span 不是 flex 容器 ⇒ 图标、文字、箭头各占一行（Owner 实测：分组标题变成三行）。 */}
      {/* 无障碍（2026-11-22）：折叠组要暴露展开态，与 governance 的 Collapsible 同口径；
          原来这里没有 aria-expanded，屏幕阅读器与用例都看不出分组是开还是关。 */}
      <RowButton
        selected={hasActiveChild}
        onClick={() => setOpen(!open)}
        aria-expanded={expanded}
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
        {/* 【2026-11-22】搜索态"自动展开"靠换 key 重挂实现：查询一变，整棵子树回到默认展开；
            用户随后仍可自行折叠（旧 forceOpen 会把这件事锁死）。清空搜索 ⇒ key 回到原值。 */}
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
          tree.map((item) => {
            const treeKey = searching ? `${item.key}|${query.trim()}` : item.key
            return item.children ? (
              <NavGroup key={treeKey} item={item} depth={0} />
            ) : (
              <NavLeaf key={treeKey} item={item} depth={0} />
            )
          })
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
