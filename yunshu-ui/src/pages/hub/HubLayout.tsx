/**
 * HubLayout —— 云枢全功能工作台导航框架（8 大栏目）
 * ------------------------------------------------
 * 导航结构（按需求提示词）：
 *   会话任务 / 全景看板 / 记忆管理 / 工具调用 / 循环工程 / 网络配置 / 装配车间 / 资产管理
 * 子栏目用二级菜单折叠展示；内容区用 <Outlet/> 渲染子路由。
 */
import { NavLink, Outlet } from 'react-router-dom'
import {
  MessageSquare, LayoutDashboard, Brain, Wrench, RefreshCw, Globe, Factory, Database,
  Activity, FileText, Server, BookOpen, Search, Boxes, Terminal, Plug, Monitor,
  HeartPulse, CalendarClock, Users, Copy, Hammer, Archive, FolderHeart, Lightbulb, Palette,
  Settings, Shield, ListTree, History, Bell, ScrollText,
} from 'lucide-react'

interface HubNav {
  path: string
  label: string
  icon: typeof MessageSquare
  children?: { path: string; label: string; icon?: typeof MessageSquare }[]
}

const NAV: HubNav[] = [
  {
    path: '/hub/session',
    label: '会话任务',
    icon: MessageSquare,
  },
  {
    path: '/hub/panorama',
    label: '全景看板',
    icon: LayoutDashboard,
    children: [
      { path: '/hub/panorama/sensors', label: '全景感知', icon: Activity },
      { path: '/hub/panorama/monitor', label: '系统监控', icon: Server },
      { path: '/hub/panorama/logs', label: '日志查看', icon: FileText },
    ],
  },
  {
    path: '/hub/memory',
    label: '记忆管理',
    icon: Brain,
    children: [
      { path: '/hub/memory/manual', label: '手动记忆', icon: Brain },
      { path: '/hub/memory/auto', label: '自动记忆', icon: RefreshCw },
      { path: '/hub/memory/skills', label: '技能库管理', icon: Hammer },
      { path: '/hub/memory/workflow', label: '工作流管理', icon: Boxes },
      { path: '/hub/memory/knowledge', label: '知识库系统', icon: BookOpen },
      { path: '/hub/memory/search', label: '搜索', icon: Search },
    ],
  },
  {
    path: '/hub/tools',
    label: '工具调用',
    icon: Wrench,
    children: [
      { path: '/hub/tools/toolset', label: '工具集', icon: Boxes },
      { path: '/hub/tools/cli', label: 'CLI 软件', icon: Terminal },
      { path: '/hub/tools/mcp', label: 'MCP 系统', icon: Plug },
      { path: '/hub/tools/computer-use', label: 'Computer Use', icon: Monitor },
    ],
  },
  {
    path: '/hub/engine',
    label: '循环工程',
    icon: RefreshCw,
    children: [
      { path: '/hub/engine/heartbeat', label: '心跳监测', icon: HeartPulse },
      { path: '/hub/engine/scheduler', label: '定时任务', icon: CalendarClock },
    ],
  },
  {
    path: '/hub/network',
    label: '网络配置',
    icon: Globe,
  },
  {
    path: '/hub/workshop',
    label: '装配车间',
    icon: Factory,
    children: [
      { path: '/hub/workshop/agents', label: '分身创建与组装', icon: Users },
      { path: '/hub/workshop/replicate', label: '系统自我复制', icon: Copy },
    ],
  },
  {
    path: '/hub/assets',
    label: '资产管理',
    icon: Database,
    children: [
      { path: '/hub/assets/memory', label: '记忆数据', icon: Database },
      { path: '/hub/assets/prompts', label: '提示词库', icon: FileText },
      { path: '/hub/assets/tools', label: '工具资源', icon: Wrench },
      { path: '/hub/assets/skills', label: '技能与工作流', icon: Hammer },
      { path: '/hub/assets/habits', label: '用户习惯', icon: FolderHeart },
      { path: '/hub/assets/inspires', label: '灵感想法', icon: Lightbulb },
      { path: '/hub/assets/hobbies', label: '爱好创造', icon: Palette },
      { path: '/hub/assets/interactions', label: '交互记忆', icon: MessageSquare },
    ],
  },
  {
    path: '/hub/admin',
    label: '系统管理',
    icon: Settings,
    children: [
      { path: '/hub/admin/dashboard', label: '仪表盘', icon: LayoutDashboard },
      { path: '/hub/admin/users', label: '用户列表', icon: Users },
      { path: '/hub/admin/roles', label: '角色权限', icon: Shield },
      { path: '/hub/admin/menus', label: '菜单管理', icon: ListTree },
      { path: '/hub/admin/audit', label: '操作审计', icon: History },
      { path: '/hub/admin/notifications', label: '消息中心', icon: Bell },
      { path: '/hub/admin/logs', label: '系统日志', icon: ScrollText },
    ],
  },
]

function NavItem({ item, depth = 0 }: { item: HubNav; depth?: number }) {
  const Icon = item.icon
  const base = 'flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors'
  const style = depth === 0
    ? 'text-slate-300 hover:bg-slate-800 hover:text-white'
    : 'pl-10 text-slate-400 hover:bg-slate-800 hover:text-white'

  return (
    <div>
      {item.children ? (
        <div className={`${base} ${style} cursor-default select-none font-medium`}>
          <Icon size={15} />
          <span>{item.label}</span>
        </div>
      ) : (
        <NavLink
          to={item.path}
          className={({ isActive }) =>
            `${base} ${style} ${isActive ? 'bg-blue-600 text-white' : ''}`
          }
        >
          <Icon size={15} />
          <span>{item.label}</span>
        </NavLink>
      )}
      {item.children && (
        <div className="mt-0.5 space-y-0.5">
          {item.children.map((c) => {
            const CIcon = c.icon
            return (
              <NavLink
                key={c.path}
                to={c.path}
                className={({ isActive }) =>
                  `${base} pl-10 text-slate-400 hover:bg-slate-800 hover:text-white ${
                    isActive ? 'bg-slate-800 text-cyan-400' : ''
                  }`
                }
              >
                {CIcon && <CIcon size={14} />}
                <span>{c.label}</span>
              </NavLink>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function HubLayout() {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-950 text-slate-200">
      {/* 侧边导航 */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-900">
        <div className="flex h-14 items-center gap-2 border-b border-slate-800 px-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600">
            <Activity size={16} className="text-white" />
          </div>
          <div>
            <div className="text-sm font-semibold text-white">云枢工作台</div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500">Hub v2</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {NAV.map((n) => (
            <NavItem key={n.path} item={n} />
          ))}
        </nav>
        <div className="border-t border-slate-800 p-3 text-[10px] text-slate-600">
          云枢 · 全功能集成工作台
        </div>
      </aside>

      {/* 内容区 */}
      <main className="min-w-0 flex-1 overflow-y-auto bg-slate-950">
        <Outlet />
      </main>
    </div>
  )
}
