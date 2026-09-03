/**
 * 云枢统一工作台 —— 导航配置中心
 * ------------------------------------------------
 * 单一数据源：导航树（9 大栏目 + 系统管理子菜单）与内容组件映射。
 * ContentPanel 根据当前选中导航项，从此处取组件渲染。
 * 所有既有功能（Hub 8 栏目 + 管理后台）统一挂载到工作台。
 */
import { lazy, type ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  MessageSquare, LayoutDashboard, Brain, Wrench, RefreshCw, Globe, Factory, Database,
  Activity, FileText, Server, BookOpen, Search, Boxes, Terminal, Plug, Monitor,
  HeartPulse, CalendarClock, Users, Copy, Hammer, FolderHeart, Lightbulb, Palette,
  Settings, Shield, ListTree, History, Bell, ScrollText, FlaskConical, Smile, Puzzle,
  Workflow, FileDown,
} from 'lucide-react'

// ═══ Code Splitting：按导航项懒加载（Vite 自动分包）═══
// 会话任务为首屏，静态导入；其余功能页 React.lazy 按需加载，
// 首屏 bundle 只含会话页，点击其他导航时才拉取对应 chunk。
import WorkbenchChatPage from '@/workbench/WorkbenchChatPage'

const PanoramaHealth = lazy(() => import('@/pages/hub/panorama/health'))
const PanoramaSensors = lazy(() => import('@/pages/hub/panorama/sensors'))
const PanoramaMonitor = lazy(() => import('@/pages/hub/panorama/monitor'))
const PanoramaLogs = lazy(() => import('@/pages/hub/panorama/logs'))
const MemoryPage = lazy(() => import('@/pages/hub/memory'))
const MemorySkills = lazy(() => import('@/pages/hub/memory/skills'))
const MemoryWorkflow = lazy(() => import('@/pages/hub/memory/workflow'))
const MemoryWorkflowVisual = lazy(() => import('@/pages/hub/memory/workflow-visual'))
const MemoryKnowledge = lazy(() => import('@/pages/hub/memory/knowledge'))
const MemorySearch = lazy(() => import('@/pages/hub/memory/search'))
const ToolsToolset = lazy(() => import('@/pages/hub/tools/toolset'))
const ToolsCli = lazy(() => import('@/pages/hub/tools/cli'))
const ToolsMcp = lazy(() => import('@/pages/hub/tools/mcp'))
const ToolsComputerUse = lazy(() => import('@/pages/hub/tools/computer-use'))
const EngineHeartbeat = lazy(() => import('@/pages/hub/engine/heartbeat'))
const EngineScheduler = lazy(() => import('@/pages/hub/engine/scheduler'))
const NetworkPage = lazy(() => import('@/pages/hub/network'))
const WorkshopAgents = lazy(() => import('@/pages/hub/workshop/agents'))
const WorkshopReplicate = lazy(() => import('@/pages/hub/workshop/replicate'))
const AssetsPage = lazy(() => import('@/pages/hub/assets'))
const HubAdminDashboard = lazy(() => import('@/pages/hub/admin/dashboard'))
const HubAdminUsers = lazy(() => import('@/pages/hub/admin/index').then((m) => ({ default: m.HubAdminUsers })))
const HubAdminRoles = lazy(() => import('@/pages/hub/admin/index').then((m) => ({ default: m.HubAdminRoles })))
const HubAdminMenus = lazy(() => import('@/pages/hub/admin/index').then((m) => ({ default: m.HubAdminMenus })))
const HubAdminAudit = lazy(() => import('@/pages/hub/admin/index').then((m) => ({ default: m.HubAdminAudit })))
const HubAdminNotifications = lazy(() => import('@/pages/hub/admin/index').then((m) => ({ default: m.HubAdminNotifications })))
const HubAdminLogs = lazy(() => import('@/pages/hub/admin/index').then((m) => ({ default: m.HubAdminLogs })))
// 数据导出：复用管理后台 Export 页（原 /export 路由已随第二套外壳摘除，收敛到系统管理栏目）
const HubAdminExport = lazy(() => import('@/pages/hub/admin/export'))
const PromptLab = lazy(() => import('@/pages/prompt-lab'))
const PersonalityPage = lazy(() => import('@/pages/hub/personality'))
const ModuleListPage = lazy(() => import('@/pages/hub/module-list'))
const PluginManagePage = lazy(() => import('@/pages/hub/plugin-manage'))

/** 导航项 */
export interface HubNavItem {
  /** 唯一键 */
  key: string
  label: string
  icon: LucideIcon
  /** 无 children 的叶子项：渲染组件 */
  component?: ComponentType
  /** 有 children 的分组项 */
  children?: HubNavItem[]
}

/** 全量导航树 */
export const HUB_NAV: HubNavItem[] = [
  { key: 'session', label: '会话任务', icon: MessageSquare, component: WorkbenchChatPage },
  { key: 'prompt-lab', label: '提示词实验室', icon: FlaskConical, component: PromptLab },
  {
    key: 'panorama', label: '全景看板', icon: LayoutDashboard,
    children: [
      { key: 'panorama/health', label: '健康仪表盘', icon: Activity, component: PanoramaHealth },
      { key: 'panorama/sensors', label: '全景感知', icon: Activity, component: PanoramaSensors },
      { key: 'panorama/monitor', label: '系统监控', icon: Server, component: PanoramaMonitor },
      { key: 'panorama/logs', label: '日志查看', icon: FileText, component: PanoramaLogs },
    ],
  },
  {
    key: 'memory', label: '记忆管理', icon: Brain,
    children: [
      { key: 'memory/manual', label: '手动记忆', icon: Brain, component: MemoryPage },
      { key: 'memory/auto', label: '自动记忆', icon: RefreshCw, component: MemoryPage },
      { key: 'memory/skills', label: '技能库管理', icon: Hammer, component: MemorySkills },
      { key: 'memory/workflow', label: '工作流管理', icon: Boxes, component: MemoryWorkflow },
      { key: 'memory/workflow-visual', label: '可视化编辑', icon: Workflow, component: MemoryWorkflowVisual },
      { key: 'memory/knowledge', label: '知识库系统', icon: BookOpen, component: MemoryKnowledge },
      { key: 'memory/search', label: '搜索', icon: Search, component: MemorySearch },
    ],
  },
  {
    key: 'tools', label: '工具调用', icon: Wrench,
    children: [
      { key: 'tools/toolset', label: '工具集', icon: Boxes, component: ToolsToolset },
      { key: 'tools/cli', label: 'CLI 软件', icon: Terminal, component: ToolsCli },
      { key: 'tools/mcp', label: 'MCP 系统', icon: Plug, component: ToolsMcp },
      { key: 'tools/computer-use', label: 'Computer Use', icon: Monitor, component: ToolsComputerUse },
    ],
  },
  {
    key: 'persona', label: '人格与提示词', icon: Smile,
    children: [
      { key: 'persona/personality', label: '人格配置', icon: Smile, component: PersonalityPage },
      // 注：原「身份提示词」「LLM 通信」两页已并入顶层「提示词实验室」
      // （身份提示词 = 实验室系统提示词线上配置区；LLM 通信 = 实验室底部监控面板），
      // 故不再作为独立导航项。
    ],
  },
  {
    key: 'engine', label: '循环工程', icon: RefreshCw,
    children: [
      { key: 'engine/heartbeat', label: '心跳监测', icon: HeartPulse, component: EngineHeartbeat },
      { key: 'engine/scheduler', label: '定时任务', icon: CalendarClock, component: EngineScheduler },
    ],
  },
  { key: 'network', label: '网络配置', icon: Globe, component: NetworkPage },
  {
    key: 'workshop', label: '装配车间', icon: Factory,
    children: [
      { key: 'workshop/agents', label: '分身创建与组装', icon: Users, component: WorkshopAgents },
      { key: 'workshop/replicate', label: '系统自我复制', icon: Copy, component: WorkshopReplicate },
    ],
  },
  {
    key: 'assets', label: '资产管理', icon: Database,
    children: [
      { key: 'assets/memory', label: '记忆数据', icon: Database, component: AssetsPage },
      { key: 'assets/prompts', label: '提示词库', icon: FileText, component: AssetsPage },
      { key: 'assets/tools', label: '工具资源', icon: Wrench, component: AssetsPage },
      { key: 'assets/skills', label: '技能与工作流', icon: Hammer, component: AssetsPage },
      { key: 'assets/habits', label: '用户习惯', icon: FolderHeart, component: AssetsPage },
      { key: 'assets/inspires', label: '灵感想法', icon: Lightbulb, component: AssetsPage },
      { key: 'assets/hobbies', label: '爱好创造', icon: Palette, component: AssetsPage },
      { key: 'assets/interactions', label: '交互记忆', icon: MessageSquare, component: AssetsPage },
    ],
  },
  {
    key: 'components', label: '系统组件', icon: Boxes,
    children: [
      { key: 'components/modules', label: '模块列表', icon: Boxes, component: ModuleListPage },
      { key: 'components/plugins', label: '插件管理', icon: Puzzle, component: PluginManagePage },
    ],
  },
  {
    key: 'admin', label: '系统管理', icon: Settings,
    children: [
      { key: 'admin/dashboard', label: '仪表盘', icon: LayoutDashboard, component: HubAdminDashboard },
      { key: 'admin/export', label: '数据导出', icon: FileDown, component: HubAdminExport },
      { key: 'admin/users', label: '用户列表', icon: Users, component: HubAdminUsers },
      { key: 'admin/roles', label: '角色权限', icon: Shield, component: HubAdminRoles },
      { key: 'admin/menus', label: '菜单管理', icon: ListTree, component: HubAdminMenus },
      { key: 'admin/audit', label: '操作审计', icon: History, component: HubAdminAudit },
      { key: 'admin/notifications', label: '消息中心', icon: Bell, component: HubAdminNotifications },
      { key: 'admin/logs', label: '系统日志', icon: ScrollText, component: HubAdminLogs },
    ],
  },
]

/**
 * 复用组件参数：多个导航项映射到同一页面组件时（assets 8 项共用 AssetsPage、
 * memory/manual | memory/auto 共用 MemoryPage），由导航 key 推导"初始分类/模式"，
 * 配合 ContentPanel 的 key={activeKey} 重挂载实现"点不同菜单渲染对应内容"（缺陷 ②）。
 * 单一来源：key 即参数语义，无需在导航配置里重复声明。
 */
export interface HubPanelParams {
  /** assets/<category>：资产页初始分类（与资产类别 key 一致：memory/prompts/tools/…） */
  initialCategory?: string
  /** memory/manual | memory/auto：记忆页初始模式 */
  mode?: 'manual' | 'auto'
}

/** 由导航 key 推导复用页面的初始参数；其余导航项无需参数（返回空对象，组件自带默认视图） */
export function derivePanelParams(activeKey: string): HubPanelParams {
  if (activeKey.startsWith('assets/')) {
    return { initialCategory: activeKey.slice('assets/'.length) }
  }
  if (activeKey === 'memory/manual' || activeKey === 'memory/auto') {
    return { mode: activeKey.slice('memory/'.length) as 'manual' | 'auto' }
  }
  return {}
}

/** 展平导航树（含分组路径，用于默认选中） */
export function flattenNav(items: HubNavItem[] = HUB_NAV): HubNavItem[] {
  return items.flatMap((it) => (it.children ? [it, ...flattenNav(it.children)] : [it]))
}

/** 按 key 查找导航项 */
export function findNavItem(key: string): HubNavItem | undefined {
  return flattenNav().find((it) => it.key === key)
}

/** 默认选中项：第一个叶子（会话任务） */
export const DEFAULT_NAV_KEY = HUB_NAV[0]?.key ?? 'session'
