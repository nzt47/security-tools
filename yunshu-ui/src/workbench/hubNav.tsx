/**
 * 云枢统一工作台 —— 导航配置中心
 * ------------------------------------------------
 * 单一数据源：导航树（阶段 3 收敛、第三轮 IA 并为 6 桶、**第四轮 IA 再并为 4 桶**）与内容组件映射。
 * 第四轮（2026-10-14）：记忆与资产 + 插件与扩展 + 网络配置并入「会话与配置」，
 * 模块列表并入「全景看板」，「系统组件」桶取消。
 * 第五轮（2026-10-14）：模块列表与系统监控同读 /api/modules/topology（双入口）⇒
 * 合并为「系统监控」的页内「模块明细」Tab，独立叶子 components/modules 移除。
 * ContentPanel 根据当前选中导航项，从此处取组件渲染。
 * 所有既有功能（Hub 8 栏目 + 管理后台）统一挂载到工作台。
 */
import { lazy, type ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  MessageSquare, LayoutDashboard, Brain, Wrench, Globe, Database,
  Activity, FileText, Server, Monitor,
  HeartPulse, Users, Hammer,
  Settings, Shield, ListTree, History, Bell, ScrollText, FlaskConical, Puzzle, Route,
  FileDown, GitBranch, Layers, ListChecks, TrendingUp, SlidersHorizontal,
} from 'lucide-react'

// ═══ Code Splitting：按导航项懒加载（Vite 自动分包）═══
// 会话任务为首屏，静态导入；其余功能页 React.lazy 按需加载，
// 首屏 bundle 只含会话页，点击其他导航时才拉取对应 chunk。
import WorkbenchChatPage from '@/workbench/WorkbenchChatPage'
// 【自动发现】src/features/<key>/index.tsx（带 manifest）在构建期静态发现，见 featureRegistry.ts
import { DISCOVERED_FEATURES } from './featureRegistry'

const PanoramaHealth = lazy(() => import('@/pages/hub/panorama/health'))
const PanoramaSensors = lazy(() => import('@/pages/hub/panorama/sensors'))
const PanoramaMonitor = lazy(() => import('@/pages/hub/panorama/monitor'))
const PanoramaLogs = lazy(() => import('@/pages/hub/panorama/logs'))
const SkillCenter = lazy(() => import('@/pages/hub/memory/skill-center'))
// 【第二轮 IA】记忆管理 = 主内容区 Tab 容器（手动 / 自动 / 知识库 / 经验库 / 全库检索）；
// 导航树只保留单一叶子项「记忆管理」（见 pages/hub/memory/hub.tsx）。
const MemoryHub = lazy(() => import('@/pages/hub/memory/hub'))
// 工具调用：子项（工具集 / CLI 软件 / MCP 系统 / Computer Use / 主线管理）已在页面内
// 收敛为 Tab（见 @/pages/hub/tools/index.tsx），导航树只保留单一叶子项「工具调用」。
// 其中「主线管理」= 能力平面档案（四平面权重 + 保底 + 效果上限）+ 实时装配预览，
// 数据源 data/agent_lines/*.yaml；后端 agent/server_routes/routes_agent_lines.py
const ToolsPage = lazy(() => import('@/pages/hub/tools'))
// 引擎：心跳监测 / 定时任务 / 执行历史 收敛为一页三 Tab（2026-10-17 IA）。
// 页面内默认落在只读的心跳视图，写操作只在「定时任务」Tab 内。
const EngineHub = lazy(() => import('@/pages/hub/engine/hub'))
const NetworkPage = lazy(() => import('@/pages/hub/network'))
// 分身创建与组装：本页含 5 个视图（分身 / 组装台 / 带走·自复制 / 主线档案 / 主线 × 四面）；
// 原独立叶子 workshop/replicate（系统自我复制）已于 2026-10-15 并入本页第 3 视图
const WorkshopAgents = lazy(() => import('@/pages/hub/workshop/agents'))
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
// 插件与扩展：原「插件管理」与「扩展中心」合并为一个导航项；2026-10-15 进一步取消 Tab，
// 插件自带 UI 直接挂在清单卡片里（extensionHost.registerPluginModule 链路保留），
// 见 pages/hub/plugins/index.tsx 与 pages/hub/plugin-manage.tsx。
const PluginsPage = lazy(() => import('@/pages/hub/plugins'))
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
  // 【阶段 3】顶层收敛（13 → 8 个语义桶）；【第三轮 IA】再并为 6 桶（记忆+资产合桶、
  // 装配车间并入会话与配置）。桶内子项 key 一律不变 ⇒ 旧深链仍有效；
  // 消失的「组 key」（engine / workshop）走 LEGACY_NAV_KEY_MAP 兼容映射。
  {
    key: 'workspace', label: '会话与配置', icon: MessageSquare,
    children: [
      { key: 'session', label: '会话任务', icon: MessageSquare, component: WorkbenchChatPage },
      // 技能中心：LLM 技能 = 模型执行，工作流技能 = 本地执行，与提示词实验室并列（分 Tab 管理）。
      { key: 'prompt-lab', label: '提示词实验室', icon: FlaskConical, component: PromptLab },
      { key: 'skills-center', label: '技能中心', icon: Hammer, component: SkillCenter },
      // 工具调用：原 5 个子项已在页面内收敛为 Tab（pages/hub/tools/index.tsx）。
      { key: 'tools', label: '工具调用', icon: Wrench, component: ToolsPage },
      // 【第三轮 IA】装配车间并入「会话与配置」：分身创建与组装 = 对话侧配置（原顶层 2 项分组消失）。
      // 【2026-10-15 IA】「系统自我复制」并入本页第 3 视图「带走 / 自复制」——后端契约本就是
      // /api/subagent/<name>/bundle（按分身打包），与「分身」是同一条主线的两端；独立叶子
      // workshop/replicate 移除 ⇒ 旧深链经 LEGACY_NAV_KEY_MAP 落到本页（禁止双入口）。
      { key: 'workshop/agents', label: '分身创建与组装', icon: Users, component: WorkshopAgents },
      // 【第四轮 IA · 2026-10-14】「记忆与资产」整桶并入本桶（顶层再少一个桶）；
      // 「插件与扩展」「网络配置」从被取消的「系统组件」桶迁入。叶子 key 一律不变
      // ⇒ 旧深链 ?panel=memory / ?panel=assets / ?panel=components/plugins / ?panel=network 仍有效。
      { key: 'memory', label: '记忆管理', icon: Brain, component: MemoryHub },
      { key: 'assets', label: '资产管理', icon: Database, component: AssetsPage },
      { key: 'components/plugins', label: '插件与扩展', icon: Puzzle, component: PluginsPage },
      { key: 'network', label: '网络配置', icon: Globe, component: NetworkPage },
    ],
  },
  {
    key: 'panorama', label: '全景看板', icon: LayoutDashboard,
    children: [
      { key: 'panorama/health', label: '健康仪表盘', icon: Activity, component: PanoramaHealth },
      { key: 'panorama/sensors', label: '全景感知', icon: Activity, component: PanoramaSensors },
      { key: 'panorama/monitor', label: '系统监控', icon: Server, component: PanoramaMonitor },
      { key: 'panorama/logs', label: '日志查看', icon: FileText, component: PanoramaLogs },
      // 【2026-10-17 IA】心跳监测 + 定时任务 + 执行历史 → 单一叶子「引擎」（页内三 Tab）。
      // 旧 key engine/heartbeat、engine/scheduler 经 LEGACY_NAV_KEY_MAP 落到本页，
      // 并保留初始 Tab 语义（组件读原始 hash 决定）。
      { key: 'engine', label: '引擎', icon: Activity, component: EngineHub },
      // 【2026-10-15 IA】治理面板 → 全景看板：这两个是**运行时运维视图**，与全景看板的
      // 运行时观测同类 —— 审计导出读**活的**链式台账 DB（实测 chain_head.count≈7.8 万），
      // 开关中心读实时登记表（实测 547 项、含 overridden）。键保持不变 ⇒ 旧深链
      // ?panel=governance/audit|settings 仍有效（derivePanelParams 按 'governance/' 前缀
      // 推导 panel，与它挂在哪个分组无关；禁止双入口）。
      { key: 'governance/audit', label: '审计导出', icon: ScrollText, component: GovernancePanels },
      { key: 'governance/settings', label: '开关中心', icon: SlidersHorizontal, component: GovernancePanels },
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
      // 【阶段 2】路由覆盖归位：数据是构建期审计产物（提交物 + 新鲜度守卫 + 棘轮），
      // 与全景看板的运行时运维视图语义不匹配，故**留在治理面板**（与「能力地图」同类）。
      // 原为 features/route-coverage 自动发现项（顶层），现已删除 manifest（禁止双入口）。
      // 【2026-10-15 IA】原同在这里的「审计导出 / 开关中心」是**运行时**视图，已上移到
      // 全景看板（见该桶内说明）；键不变，旧深链照旧生效。
      { key: 'governance/route-coverage', label: '路由覆盖', icon: Route, component: GovernancePanels },
    ],
  },
  // 【第三轮 IA】原顶层叶子「资产管理」已并入上面的「记忆与资产」桶；
  // 原顶层分组「装配车间」的两个子项已并入「会话与配置」。二者不再占顶层槽位。
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
  // 【自动发现】features/<key>/index.tsx（export default + manifest）—— 新功能零清单进导航
  ...DISCOVERED_FEATURES,
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
    | 'memory' | 'audit' | 'settings' | 'route-coverage'
}

/** 由导航 key 推导复用页面的初始参数；其余导航项无需参数（返回空对象，组件自带默认视图） */
export function derivePanelParams(activeKey: string): HubPanelParams {
  // 【第二轮 IA】assets / memory 已收敛为叶子（子项在页内 Tab / 分类切换），
  // 不再由导航 key 推导 initialCategory / mode。
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

/**
 * 默认选中项：第一个**叶子**（会话任务）。
 *
 * 【阶段 3 修正】原实现取 HUB_NAV[0].key —— 顶层收敛后 HUB_NAV[0] 是分组
 * 「会话与配置」（无 component），会让工作台默认打开空态（DetachedChatApp 回归用例暴露此点）；
 * 改为展平后取第一个叶子，语义与注释「第一个叶子」一致。
 */
export const DEFAULT_NAV_KEY = flattenNav(HUB_NAV).find((it) => !it.children)?.key ?? 'session'

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
 * **启动瞬间**的深链 panel（在导航层改写地址栏之前捕获）。
 *
 * 【为什么必须在模块加载期取】引擎页是懒加载组件，挂载时 `engine/scheduler` 已被
 * 归一成 `engine`；页内再读 hash 只能拿到归一后的值 ⇒ 旧深链会丢掉 Tab 意图。
 */
export const BOOT_PANEL: string = (() => {
  try {
    const raw = typeof window !== 'undefined' ? window.location.hash : ''
    const q = raw.includes('?') ? raw.slice(raw.indexOf('?') + 1) : ''
    return new URLSearchParams(q).get(NAV_PANEL_PARAM) || ''
  } catch {
    return ''
  }
})()

// ════════════════════════════════════════════════════════════
//  旧深链兼容层（2026-10-11 UI 信息架构整合 · 阶段 0）
// ════════════════════════════════════════════════════════════

/**
 * 旧深链 key → 新 key 映射表。
 *
 * 【为什么需要】深链 `?panel=<key>` 是**外部契约**：收藏、文档、他人链接都按旧 key
 * 写死。信息架构调整（合并/移动/删除导航项）后，这些链接会指向树上不存在的 key，
 * 被 navKeyFromSearch 拒绝后落回默认页 —— 人看到的是「工作台坏了」，而不是「链接过时」。
 *
 * 依据：docs/proposals/ui_information_architecture_review_20261011.md §6「阶段 0」。
 */
export const LEGACY_NAV_KEY_MAP: Readonly<Record<string, string>> = Object.freeze({
  // 人格配置并入提示词实验室（阶段 1.1）
  'persona/personality': 'prompt-lab',
  // 经验库先移入记忆管理（阶段 1.3），第二轮再随记忆管理收敛为页内 Tab
  'experience': 'memory',
  // 扩展中心并入系统组件 · 插件管理（阶段 1.2）
  'extensions': 'components/plugins',
  // 【2026-10-17 IA】引擎三合一：心跳/定时任务/执行历史 → 叶子 engine。
  // 旧子项 key 落到本页；engine 自身已是真实叶子，无需再映射。
  'engine/heartbeat': 'engine',
  'engine/scheduler': 'engine',
  // 【第三轮 IA】装配车间并入会话与配置：组 key 消失，落到其原第一个子项
  'workshop': 'workshop/agents',
  // 【2026-10-15 IA】「系统自我复制」并入「分身创建与组装」第 3 视图（带走 / 自复制）：
  //   独立叶子消失 ⇒ 深链落到本页（页内视图记忆会接续用户上次看的视图）
  'workshop/replicate': 'workshop/agents',
  // 【第四轮 IA · 2026-10-14】「记忆与资产」「系统组件」两个桶被取消：
  //   组 key 消失 ⇒ 落到各自的原第一个子项（叶子 key 不变，深链继续有效）。
  'memory-assets': 'memory',
  // 【第五轮 IA · 2026-10-14】「模块列表」并入「系统监控」（两者同读 /api/modules/topology，
  //   是本仓明令避免的"双入口"）：独立叶子 components/modules 消失 ⇒ 连同旧组 key
  //   components 一起落到 panorama/monitor。
  // 【2026-10-15】该页进一步把「总览 / 模块明细」两个 Tab 合为一页（同源同口径，
  //   不再有 Tab 切换），旧深链仍落到本页。
  'components': 'panorama/monitor',
  'components/modules': 'panorama/monitor',
  // 【第二轮 IA】记忆管理子项 → 页内 Tab（落到「记忆管理」页，Tab 记忆接续上次选择）
  'memory/manual': 'memory',
  'memory/auto': 'memory',
  'memory/knowledge': 'memory',
  'memory/experience': 'memory',
  'memory/search': 'memory',
  // 【第二轮 IA】资产管理子项 → 同一面板（页内分类切换）
  'assets/memory': 'assets',
  'assets/prompts': 'assets',
  'assets/tools': 'assets',
  'assets/skills': 'assets',
  'assets/habits': 'assets',
  'assets/inspires': 'assets',
  'assets/hobbies': 'assets',
  'assets/interactions': 'assets',
})

/** key 是否存在于给定导航树（items 可注入，便于测试「迁移后」的树） */
export function isNavKey(key: string, items: HubNavItem[] = HUB_NAV): boolean {
  return flattenNav(items).some((it) => it.key === key)
}

/**
 * 把深链 key 规范化成导航树里**真实存在**的 key。
 *
 * 语义（三条，缺一不可）：
 *   1. key 本身在树里 ⇒ 原样返回。迁移尚未落地时旧 key 仍在，行为与从前完全一致
 *      （不会把旧入口提前指向还没做好的新页，例如阶段 1.3 之前 memory/experience
 *      还不存在，此时 `?panel=experience` 必须继续打开顶层经验库）；
 *   2. key 已不在树里、但命中 LEGACY_NAV_KEY_MAP 且新 key 存在 ⇒ 落到新 key（兼容生效）；
 *   3. 两者都不在树里 ⇒ 空串（navKeyFromSearch 继续拒绝非法/拼错 key，不渲染空白页）。
 */
export function resolveNavKey(key: string, items: HubNavItem[] = HUB_NAV): string {
  if (!key) return ''
  if (isNavKey(key, items)) return key
  const mapped = LEGACY_NAV_KEY_MAP[key]
  return mapped && isNavKey(mapped, items) ? mapped : ''
}

/**
 * 从 URL search 取导航 key；**不合法（不在导航树里）一律返回空串**
 *
 * 【为什么必须校验】地址栏是可以手改的。若不校验，一个拼错的 `?panel=` 会把
 * activeKey 设成一个不存在的 key ⇒ ContentPanel 渲染兜底空白页，人看到的是
 * "工作台坏了"，而不是"链接写错了"。
 *
 * 【与兼容层的关系】先经 resolveNavKey 做旧 key → 新 key 归一，再保持「非法 key
 * 返回空串」语义不变（见 LEGACY_NAV_KEY_MAP / resolveNavKey）。
 */
export function navKeyFromSearch(search: string): string {
  try {
    const key = new URLSearchParams(search).get(NAV_PANEL_PARAM) ?? ''
    return resolveNavKey(key)
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
