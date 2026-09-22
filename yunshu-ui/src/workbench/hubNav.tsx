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
  Activity, FileText, Server, BookOpen, Search, Boxes, Monitor,
  HeartPulse, CalendarClock, Users, Copy, Hammer, FolderHeart, Lightbulb, Palette,
  Settings, Shield, ListTree, History, Bell, ScrollText, FlaskConical, Smile, Puzzle,
  FileDown, GitBranch, Layers, ListChecks, TrendingUp, SlidersHorizontal,
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
const SkillCenter = lazy(() => import('@/pages/hub/memory/skill-center'))
const MemoryKnowledge = lazy(() => import('@/pages/hub/memory/knowledge'))
const MemorySearch = lazy(() => import('@/pages/hub/memory/search'))
// 工具调用：子项（工具集 / CLI 软件 / MCP 系统 / Computer Use / 主线管理）已在页面内
// 收敛为 Tab（见 @/pages/hub/tools/index.tsx），导航树只保留单一叶子项「工具调用」。
// 其中「主线管理」= 能力平面档案（四平面权重 + 保底 + 效果上限）+ 实时装配预览，
// 数据源 data/agent_lines/*.yaml；后端 agent/server_routes/routes_agent_lines.py
const ToolsPage = lazy(() => import('@/pages/hub/tools'))
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
// 治理面板（v7.2 §7 六面板 + 审计导出）：**工作台内扩展，不建独立 Web App**（UI 五坑④）。
// 单一组件 + `panel` 参数（同 ContentPanel 的"key 即参数语义"约定，见 derivePanelParams）。
const GovernancePanels = lazy(() => import('@/pages/hub/governance'))

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
  // 技能中心：从「记忆管理」上提为顶层导航项，与「提示词实验室」并列
  // （LLM 技能 = 模型执行，工作流技能 = 本地执行，分 Tab 管理）。
  { key: 'skills-center', label: '技能中心', icon: Hammer, component: SkillCenter },
  // 工具调用：原 5 个子项（工具集 / CLI 软件 / MCP 系统 / Computer Use / 主线管理）
  // 已收敛为主内容区 Tab（见 @/pages/hub/tools/index.tsx），导航树只留单一叶子项 ——
  // 与「提示词实验室 / 技能中心」同款层级：导航列模块，模块内的子项用 Tab 并列。
  // 位置：紧邻「提示词实验室 / 技能中心」之后、**全景看板之前**（配置类模块在前，
  // 看板/管理类在后）。
  { key: 'tools', label: '工具调用', icon: Wrench, component: ToolsPage },
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
    // v7.2 §7 六面板（P0 展开 / P1·P2 折叠；自愈事故有事故时自动展开）
    // 全部挂在本工作台内，不另起 Web App（UI 五坑④）
    // 【2026-09-22 上移】原排在「系统组件」之后（14 项里的第 13 项）⇒ 侧栏要滚到底
    // 才看得见，而侧栏滚动条只有 6px / 25% 透明度，操作者实测"找不到治理面板"
    // （连带找不到审批收件箱 ⇒ 审批单据挂单后没人能批）。现上移到「全景看板」之后：
    // 治理面板是 P0 操作面（审批/开关/自愈），应当一屏可见。
    key: 'governance', label: '治理面板', icon: Shield,
    children: [
      { key: 'governance/pipeline', label: '消化流水线', icon: GitBranch, component: GovernancePanels },
      { key: 'governance/approvals', label: '审批收件箱', icon: ListChecks, component: GovernancePanels },
      { key: 'governance/capabilities', label: '能力地图', icon: Layers, component: GovernancePanels },
      { key: 'governance/roi', label: '成本 ROI', icon: TrendingUp, component: GovernancePanels },
      { key: 'governance/incidents', label: '自愈事故', icon: HeartPulse, component: GovernancePanels },
      { key: 'governance/memory', label: '记忆技能库', icon: Brain, component: GovernancePanels },
      { key: 'governance/audit', label: '审计导出', icon: ScrollText, component: GovernancePanels },
      // TASK-S7-01「开关中心」：登记表全量（分类分组 + 搜索 + 风险分级 + 生效来源）
      { key: 'governance/settings', label: '开关中心', icon: SlidersHorizontal, component: GovernancePanels },
    ],
  },
  {
    key: 'memory', label: '记忆管理', icon: Brain,
    children: [
      { key: 'memory/manual', label: '手动记忆', icon: Brain, component: MemoryPage },
      { key: 'memory/auto', label: '自动记忆', icon: RefreshCw, component: MemoryPage },
      // 注：技能中心已上提为顶层导航项（与「提示词实验室」并列），不再挂在记忆管理下。
      { key: 'memory/knowledge', label: '知识库系统', icon: BookOpen, component: MemoryKnowledge },
      { key: 'memory/search', label: '搜索', icon: Search, component: MemorySearch },
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
  /** governance/<panel>：治理面板栏目（六面板 + 审计导出 + 开关中心；单组件按参数渲染） */
  panel?: 'pipeline' | 'capabilities' | 'approvals' | 'roi' | 'incidents'
    | 'memory' | 'audit' | 'settings'
}

/** 由导航 key 推导复用页面的初始参数；其余导航项无需参数（返回空对象，组件自带默认视图） */
export function derivePanelParams(activeKey: string): HubPanelParams {
  if (activeKey.startsWith('assets/')) {
    return { initialCategory: activeKey.slice('assets/'.length) }
  }
  if (activeKey === 'memory/manual' || activeKey === 'memory/auto') {
    return { mode: activeKey.slice('memory/'.length) as 'manual' | 'auto' }
  }
  if (activeKey.startsWith('governance/')) {
    return { panel: activeKey.slice('governance/'.length) as HubPanelParams['panel'] }
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

// ════════════════════════════════════════════════════════════
//  导航检索 + 深链（2026-09-22）
// ════════════════════════════════════════════════════════════

/**
 * 按关键词过滤导航树（侧栏搜索框的唯一实现）
 *
 * 【为什么分组自身命中就整组保留】用户输入「治理」时想要的是**整个治理面板**
 * （他可能还不知道里面叫「审批收件箱」），只回一个子项等于把路又堵一半。
 * 反之只命中某个子项（如输入「审批」）时，只保留该子项——分组标题不参与展开噪音。
 *
 * 【为什么返回新树而不是就地改】HUB_NAV 是模块级单一数据源；就地过滤会让"搜索一次
 * 之后导航永久变短"，这是最难排查的一类状态污染。
 */
export function filterNav(items: HubNavItem[], query: string): HubNavItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  const hit = (text: string) => text.toLowerCase().includes(q)
  const walk = (nodes: HubNavItem[]): HubNavItem[] => {
    const out: HubNavItem[] = []
    for (const node of nodes) {
      if (hit(node.label) || hit(node.key)) {
        out.push(node)
        continue
      }
      if (node.children) {
        const kids = walk(node.children)
        if (kids.length) out.push({ ...node, children: kids })
      }
    }
    return out
  }
  return walk(items)
}

/** 深链参数名：`#/workbench?panel=governance/approvals` */
export const NAV_PANEL_PARAM = 'panel'

/**
 * 从 URL search 取导航 key；**不合法（不在导航树里）一律返回空串**
 *
 * 【为什么必须校验】地址栏是可以手改的。若不校验，一个拼错的 `?panel=` 会把
 * activeKey 设成一个不存在的 key ⇒ ContentPanel 渲染兜底空白页，人看到的是
 * "工作台坏了"，而不是"链接写错了"。
 */
export function navKeyFromSearch(search: string): string {
  try {
    const key = new URLSearchParams(search).get(NAV_PANEL_PARAM) ?? ''
    return key && findNavItem(key) ? key : ''
  } catch {
    return ''
  }
}

/** 把导航 key 写进 URL search（保留其它参数；key 为空则删除该参数） */
export function searchForNavKey(search: string, key: string): string {
  try {
    const params = new URLSearchParams(search)
    if (key) {
      params.set(NAV_PANEL_PARAM, key)
    } else {
      params.delete(NAV_PANEL_PARAM)
    }
    const next = params.toString()
    return next ? `?${next}` : ''
  } catch {
    return ''
  }
}
