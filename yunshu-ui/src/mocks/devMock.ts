/**
 * 本地开发 Mock（仅 dev server 生效）
 * 在 Vite dev server 层拦截登录 / 用户信息接口，返回与后端一致的 {code, data, message} 结构，
 * 使前端在无后端时即可跑通登录流程。
 * 联调真实后端时，将 .env.development 中 VITE_MOCK_API 置为 false（或删除本插件）。
 */
import type { Plugin } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { EXPORT_MOCK_USERS } from './exportMock'

interface MockUser {
  id: number
  username: string
  nickname: string
  email: string
  avatar?: string
  /** 角色标识：路由权限校验按此字段判断（改角色即可演示菜单显隐 / 403） */
  role?: string
  permissions?: string[]
}

/** 内联 SVG 头像（避免依赖外部资源，dev 环境即可展示 Header 头像） */
const MOCK_AVATAR =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">' +
      '<rect width="64" height="64" rx="32" fill="#2563eb"/>' +
      '<text x="32" y="42" font-family="sans-serif" font-size="28" fill="#ffffff" text-anchor="middle">管</text>' +
      '</svg>'
  )

/** 管理员账号（admin/123456）：角色 admin，可见全部菜单（含系统管理）；system:log:export 为系统日志导出权限码 */
const MOCK_ADMIN: MockUser = {
  id: 1,
  username: 'admin',
  nickname: '本地管理员',
  email: 'admin@yunshu.local',
  avatar: MOCK_AVATAR,
  role: 'admin',
  permissions: ['dashboard:view', 'workbench:use', 'prompt-lab:use', 'system:log:export'],
}

/** 普通用户账号（user/123456）：角色 user，拥有 system:view（系统管理分组/系统日志）
 *  与 system:notification:view（消息中心）；无 system:user:view 等（用户列表/角色权限等隐藏） */
const MOCK_USER: MockUser = {
  id: 2,
  username: 'user',
  nickname: '普通用户',
  email: 'user@yunshu.local',
  avatar: MOCK_AVATAR,
  role: 'user',
  permissions: ['dashboard:view', 'workbench:use', 'system:view', 'system:notification:view'],
}

/** 按用户名取 mock 用户（login 已校验账号，此处不会遇到未知用户名） */
function getUserByUsername(username: string): MockUser {
  return username === 'user' ? MOCK_USER : MOCK_ADMIN
}

// ---------- 菜单树（/api/auth/menus，模拟后端按角色过滤下发） ----------

/** 后端下发的菜单树节点（模拟真实后端 /api/auth/menus 的返回结构） */
interface MockMenuNode {
  path: string
  title: string
  /** 图标名称，前端 MENU_ICON_MAP 映射为组件 */
  icon?: string
  /** 权限码：后端按角色过滤后下发，前端不再判定 */
  authority?: string
  children?: MockMenuNode[]
}

/** 全量菜单配置（模拟后端数据库中的菜单表，authority 与 MOCK_PERMISSIONS 权限码对齐） */
const ALL_MENUS: MockMenuNode[] = [
  { path: '/', title: '仪表盘', icon: 'dashboard' },
  { path: '/workbench', title: '工作台', icon: 'workbench' },
  { path: '/demo', title: '组件演示', icon: 'demo' },
  { path: '/export', title: '数据导出', icon: 'export' },
  {
    path: '/system',
    title: '系统管理',
    icon: 'system',
    authority: 'system:view',
    children: [
      { path: '/system/user', title: '用户列表', icon: 'user', authority: 'system:user:view' },
      { path: '/system/role', title: '角色权限', icon: 'role', authority: 'system:role:view' },
      { path: '/system/menu', title: '菜单管理', icon: 'menu', authority: 'system:role:view' },
      { path: '/system/audit', title: '操作审计', icon: 'audit', authority: 'system:audit:view' },
      { path: '/system/notification', title: '消息中心', icon: 'notification', authority: 'system:notification:view' },
      { path: '/system/log', title: '系统日志', icon: 'log', authority: 'system:view' },
    ],
  },
]

/** 模拟后端按角色/权限过滤菜单树（admin 通配，其余角色按权限码命中；子项全隐藏的分组一并剔除） */
function filterMenusForRole(
  nodes: MockMenuNode[],
  role: string | undefined,
  permissions: string[] = [],
): MockMenuNode[] {
  return nodes
    .filter((node) => !node.authority || role === 'admin' || permissions.includes(node.authority))
    .map((node) => ({
      ...node,
      children: node.children ? filterMenusForRole(node.children, role, permissions) : undefined,
    }))
    .filter((node) => !node.children || node.children.length > 0)
}

/** 用户列表项（Mock 结构对齐前端 UserListItem） */
interface MockListUser {
  id: number
  username: string
  email: string
  role: string
  /** 1 启用 / 0 禁用 */
  status: 0 | 1
  createdAt: string
}

/** 生成 26 条 mock 用户：覆盖多页、关键字搜索、分页边界 */
const MOCK_USERS: MockListUser[] = Array.from({ length: 26 }, (_, i) => {
  const id = i + 1
  return {
    id,
    username: id === 1 ? 'admin' : `user${String(id).padStart(2, '0')}`,
    email: `user${id}@yunshu.local`,
    role: id === 1 ? 'admin' : id % 3 === 0 ? 'manager' : 'user',
    status: id % 5 === 0 ? 0 : 1,
    createdAt: `2026-0${(id % 9) + 1}-${String((id % 27) + 1).padStart(2, '0')} 10:30:00`,
  }
})

// ---------- 角色与权限（M2 RBAC mock，真实后端未实现前兜底） ----------

/** 权限码列表（分组，与 routes.tsx 的 authority / userInfo.permissions 对齐） */
const MOCK_PERMISSIONS: Array<{ code: string; label: string; group: string }> = [
  { code: 'dashboard:view', label: '查看仪表盘', group: '仪表盘' },
  { code: 'workbench:use', label: '使用工作台', group: '工作台' },
  { code: 'prompt-lab:use', label: '使用提示词实验室', group: '组件' },
  { code: 'system:view', label: '查看系统管理', group: '系统管理' },
  { code: 'system:user:view', label: '查看用户列表', group: '系统管理' },
  { code: 'system:user:edit', label: '编辑用户', group: '系统管理' },
  { code: 'system:role:view', label: '查看角色列表', group: '系统管理' },
  { code: 'system:role:edit', label: '编辑角色', group: '系统管理' },
  { code: 'system:audit:view', label: '查看操作审计', group: '系统管理' },
]

/** 角色项（结构对齐前端 RoleItem） */
interface MockRole {
  id: number
  name: string
  label: string
  description: string
  permissions: string[]
  /** 数据范围：all 全部 / dept 本部门 / self 仅本人 */
  dataScope: 'all' | 'dept' | 'self'
  createdAt: string
}

/** 内置角色：admin 全权限、manager 中权限、user 基础权限（可变，支持 CRUD） */
const MOCK_ROLES: MockRole[] = [
  {
    id: 1,
    name: 'admin',
    label: '管理员',
    description: '系统超级管理员，拥有全部权限',
    permissions: MOCK_PERMISSIONS.map((p) => p.code),
    dataScope: 'all',
    createdAt: '2026-08-01 10:00:00',
  },
  {
    id: 2,
    name: 'manager',
    label: '经理',
    description: '可管理用户与角色',
    permissions: ['dashboard:view', 'workbench:use', 'system:view', 'system:user:view', 'system:user:edit', 'system:role:view'],
    dataScope: 'dept',
    createdAt: '2026-08-02 11:00:00',
  },
  {
    id: 3,
    name: 'user',
    label: '普通用户',
    description: '仅基础功能',
    permissions: ['dashboard:view', 'workbench:use'],
    dataScope: 'self',
    createdAt: '2026-08-03 12:00:00',
  },
]

// ---------- 菜单管理（M3 mock） ----------

/** 菜单树（结构对齐前端 MenuItem） */
let MOCK_MENUS: Array<{
  id: number
  parentId: number
  title: string
  path: string
  icon: string
  authority: string
  order: number
  hideInMenu: boolean
}> = [
  { id: 1, parentId: 0, title: '仪表盘', path: '/', icon: 'LayoutDashboard', authority: '', order: 1, hideInMenu: false },
  { id: 2, parentId: 0, title: '工作台', path: '/workbench', icon: 'Workflow', authority: '', order: 2, hideInMenu: false },
  { id: 3, parentId: 0, title: '系统管理', path: '/system', icon: 'Settings', authority: 'system:view', order: 10, hideInMenu: false },
  { id: 4, parentId: 3, title: '用户列表', path: '/system/user', icon: 'Users', authority: 'system:user:view', order: 1, hideInMenu: false },
  { id: 5, parentId: 3, title: '角色权限', path: '/system/role', icon: 'ShieldCheck', authority: 'system:role:view', order: 2, hideInMenu: false },
]

/** 组装菜单树（按 parentId，保持 order 升序） */
function buildMenuTree(): Array<Record<string, unknown> & { children?: unknown[] }> {
  const sorted = [...MOCK_MENUS].sort((a, b) => a.order - b.order)
  const map = new Map<number, Record<string, unknown> & { children?: unknown[] }>()
  sorted.forEach((m) => map.set(m.id, { ...m, children: [] }))
  const roots: Array<Record<string, unknown> & { children?: unknown[] }> = []
  map.forEach((node) => {
    const parentId = node.parentId as number
    if (parentId === 0 || !map.has(parentId)) roots.push(node)
    else (map.get(parentId)!.children as unknown[]).push(node)
  })
  return roots
}

// ---------- 操作审计日志（M4 mock） ----------

/** 审计日志项（结构对齐前端 AuditLogItem） */
interface MockAuditLog {
  id: number
  traceId: string
  operator: string
  action: string
  target: string
  result: 'success' | 'fail'
  ip: string
  detail: string
  createdAt: string
}

const AUDIT_OPERATORS = ['admin', 'manager', 'user']
const AUDIT_ACTIONS = ['login', 'create', 'update', 'delete', 'export']

/** 生成 32 条审计日志：覆盖多操作人/类型/结果/分页边界 */
const MOCK_AUDIT_LOGS: MockAuditLog[] = Array.from({ length: 32 }, (_, i) => {
  const idx = 32 - i
  const action = AUDIT_ACTIONS[i % AUDIT_ACTIONS.length]
  const verb = action === 'delete' ? '删除' : action === 'create' ? '新增' : action === 'export' ? '导出' : action === 'update' ? '更新' : '登录'
  return {
    id: idx,
    traceId: `trace-${idx.toString().padStart(4, '0')}`,
    operator: AUDIT_OPERATORS[i % AUDIT_OPERATORS.length],
    action,
    target: action === 'login' ? '登录系统' : `${verb}用户 user${String((i % 20) + 1).padStart(2, '0')}`,
    result: i % 7 === 0 ? 'fail' : 'success',
    ip: `10.0.${i % 4}.${(i % 250) + 1}`,
    detail: i % 7 === 0 ? '权限不足或数据不存在' : '',
    createdAt: `2026-08-${String((i % 20) + 1).padStart(2, '0')} ${String(i % 24).padStart(2, '0')}:${String((i * 3) % 60).padStart(2, '0')}:00`,
  }
})

/** Mock 通知项（结构对齐前端 NotificationItem） */
interface MockNotification {
  id: number
  type: 'system' | 'audit' | 'approval' | 'alert'
  title: string
  content: string
  read: boolean
  createdAt: string
}

const NOTIFICATION_TITLES: Record<MockNotification['type'], string[]> = {
  system: ['系统例行维护公告', '版本升级通知', '新功能上线说明'],
  audit: ['检测到异常登录行为', '高风险操作提醒', '审计日志导出完成'],
  approval: ['待审批：新用户开通申请', '待审批：角色权限变更', '待审批：数据导出申请'],
  alert: ['接口错误率超过阈值', '磁盘空间使用告警', '登录失败次数异常'],
}

/** 生成 24 条 mock 通知：覆盖多类型/已读未读/分页边界 */
const MOCK_NOTIFICATIONS: MockNotification[] = Array.from({ length: 24 }, (_, i) => {
  const idx = 24 - i
  const types: MockNotification['type'][] = ['system', 'audit', 'approval', 'alert']
  const type = types[i % types.length]
  const titles = NOTIFICATION_TITLES[type]
  return {
    id: idx,
    type,
    title: titles[i % titles.length],
    content: `【${idx}】${type === 'system' ? '系统将于维护窗口执行例行检查' : type === 'audit' ? '检测到来自 10.0.0.1 的非常规操作' : type === 'approval' ? '申请人：user' + String((i % 20) + 1).padStart(2, '0') + '，请及时处理' : '累计触发 3 次阈值，请关注'}`,
    read: i % 3 !== 0,
    createdAt: `2026-08-${String((i % 20) + 1).padStart(2, '0')} ${String(i % 24).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}:00`,
  }
})

/** 模拟网络延迟，让 Loading 状态可见 */
const MOCK_DELAY = 500

function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (chunk) => {
      raw += chunk
    })
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(body))
}

export function mockApiPlugin(options: { loginReturnUser: boolean }): Plugin {
  return {
    name: 'yunshu-mock-api',
    apply: 'serve', // 仅开发服务器生效
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? ''

        // 登录：POST /api/auth/login
        if (req.method === 'POST' && url === '/api/auth/login') {
          readBody(req).then((body) => {
            const { username, password } = body
            if (!username || !password) {
              sendJson(res, 200, { code: 400, data: null, message: '用户名或密码不能为空' })
              return
            }
            setTimeout(() => {
              // 模拟账号校验：admin/123456 管理员、user/123456 普通用户，其余返回业务错误
              // 触发拦截器 Toast + 登录页页内错误提示
              const isAdmin = username === 'admin'
              const isUser = username === 'user'
              if ((!isAdmin && !isUser) || password !== '123456') {
                sendJson(res, 200, { code: 400, data: null, message: '用户名或密码错误' })
                return
              }
              const mockUser = getUserByUsername(String(username))
              console.log(`[mock] 登录成功：${mockUser.username}（role=${mockUser.role}）`)
              sendJson(res, 200, {
                code: 200,
                data: {
                  // 【Why】token 编码用户名，/user/info 据此还原账号角色，刷新页面后角色不漂移
                  token: `mock-token-${mockUser.username}-${Date.now()}`,
                  // 【Why】loginReturnUser 由 .env 的 VITE_MOCK_LOGIN_RETURN_USER 控制：
                  // false 时不返回 user，让 MainLayout 走"登录后初始化"拉取路径（可验证骨架屏）；
                  // true 时模拟真实后端登录直接携带用户信息，MainLayout 跳过拉取
                  ...(options.loginReturnUser ? { user: mockUser } : {}),
                },
                message: 'success',
              })
            }, MOCK_DELAY)
          })
          return
        }

        // 用户信息：GET /api/user/info（校验 Authorization: Bearer 头）
        if (req.method === 'GET' && url === '/api/user/info') {
          const auth = req.headers.authorization
          if (!auth || !auth.startsWith('Bearer ')) {
            sendJson(res, 200, { code: 401, data: null, message: '未登录或登录已过期' })
            return
          }
          const token = auth.slice('Bearer '.length).trim()
          // 模拟 Token 失效：以 expired- 开头的 Token 返回 HTTP 401，
          // 触发响应拦截器"清除凭证 + 跳转登录页"分支
          if (token.startsWith('expired-')) {
            sendJson(res, 401, { code: 401, data: null, message: 'Token 已过期，请重新登录' })
            return
          }
          // 从 token 还原登录账号（格式：mock-token-<username>-<timestamp>）；
          // 旧格式 token 解析不到用户名 → 视为未登录，触发重新登录
          const username = /^mock-token-(.+)-(\d+)$/.exec(token)?.[1]
          if (!username) {
            sendJson(res, 200, { code: 401, data: null, message: '未登录或登录已过期' })
            return
          }
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: getUserByUsername(username), message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 菜单树：GET /api/auth/menus（按 token 还原账号角色后，过滤下发可见菜单）
        if (req.method === 'GET' && url === '/api/auth/menus') {
          const auth = req.headers.authorization
          if (!auth || !auth.startsWith('Bearer ')) {
            sendJson(res, 200, { code: 401, data: null, message: '未登录或登录已过期' })
            return
          }
          const token = auth.slice('Bearer '.length).trim()
          if (token.startsWith('expired-')) {
            sendJson(res, 401, { code: 401, data: null, message: 'Token 已过期，请重新登录' })
            return
          }
          const username = /^mock-token-(.+)-(\d+)$/.exec(token)?.[1]
          if (!username) {
            sendJson(res, 200, { code: 401, data: null, message: '未登录或登录已过期' })
            return
          }
          const user = getUserByUsername(username)
          setTimeout(() => {
            sendJson(res, 200, {
              code: 200,
              data: filterMenusForRole(ALL_MENUS, user.role, user.permissions),
              message: 'success',
            })
          }, MOCK_DELAY)
          return
        }

        // 用户列表：GET /api/user/list?page=1&pageSize=10&keyword=xx
        if (req.method === 'GET' && url.startsWith('/api/user/list')) {
          const searchParams = new URL(url, 'http://localhost').searchParams
          const page = Math.max(1, Number(searchParams.get('page') ?? 1))
          const pageSize = Math.max(1, Number(searchParams.get('pageSize') ?? 10))
          const keyword = (searchParams.get('keyword') ?? '').trim()
          const matched = keyword
            ? MOCK_USERS.filter((u) => u.username.includes(keyword) || u.email.includes(keyword))
            : MOCK_USERS
          const list = matched.slice((page - 1) * pageSize, page * pageSize)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: { list, total: matched.length }, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 删除用户：DELETE /api/user/:id
        const deleteMatch = /^\/api\/user\/(\d+)$/.exec(url)
        if (req.method === 'DELETE' && deleteMatch) {
          const id = Number(deleteMatch[1])
          const idx = MOCK_USERS.findIndex((u) => u.id === id)
          if (idx === -1) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '用户不存在' }), MOCK_DELAY)
            return
          }
          MOCK_USERS.splice(idx, 1)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: null, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 新增用户：POST /api/user（用户名必填且唯一）
        if (req.method === 'POST' && url === '/api/user') {
          readBody(req).then((body) => {
            const username = String(body.username ?? '').trim()
            if (!username) {
              sendJson(res, 200, { code: 400, data: null, message: '用户名不能为空' })
              return
            }
            if (MOCK_USERS.some((u) => u.username === username)) {
              sendJson(res, 200, { code: 400, data: null, message: `用户名 ${username} 已存在` })
              return
            }
            const role = String(body.role ?? 'user')
            const newUser: MockListUser = {
              id: Math.max(...MOCK_USERS.map((u) => u.id)) + 1,
              username,
              email: String(body.email ?? '').trim(),
              role: ['admin', 'manager', 'user'].includes(role) ? role : 'user',
              status: body.status === 0 || body.status === '0' ? 0 : 1,
              createdAt: new Date().toISOString().slice(0, 19).replace('T', ' '),
            }
            MOCK_USERS.push(newUser)
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: newUser, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 编辑用户：PUT /api/user/:id（邮箱/角色/状态可改，用户名不可改）
        const putMatch = /^\/api\/user\/(\d+)$/.exec(url)
        if (req.method === 'PUT' && putMatch) {
          const id = Number(putMatch[1])
          const target = MOCK_USERS.find((u) => u.id === id)
          if (!target) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '用户不存在' }), MOCK_DELAY)
            return
          }
          readBody(req).then((body) => {
            if ('email' in body) target.email = String(body.email ?? '').trim()
            if ('role' in body && ['admin', 'manager', 'user'].includes(String(body.role))) {
              target.role = String(body.role)
            }
            if ('status' in body) target.status = body.status === 0 || body.status === '0' ? 0 : 1
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: target, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 权限码列表：GET /api/permissions（分组，供角色权限分配界面）
        if (req.method === 'GET' && url === '/api/permissions') {
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: MOCK_PERMISSIONS, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 角色列表：GET /api/role/list?page=1&pageSize=10&keyword=xx
        if (req.method === 'GET' && url.startsWith('/api/role/list')) {
          const searchParams = new URL(url, 'http://localhost').searchParams
          const page = Math.max(1, Number(searchParams.get('page') ?? 1))
          const pageSize = Math.max(1, Number(searchParams.get('pageSize') ?? 10))
          const keyword = (searchParams.get('keyword') ?? '').trim()
          const matched = keyword
            ? MOCK_ROLES.filter((r) => r.name.includes(keyword) || r.label.includes(keyword))
            : MOCK_ROLES
          const list = matched.slice((page - 1) * pageSize, page * pageSize)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: { list, total: matched.length }, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 新增角色：POST /api/role（name 唯一）
        if (req.method === 'POST' && url === '/api/role') {
          readBody(req).then((body) => {
            const name = String(body.name ?? '').trim()
            const label = String(body.label ?? '').trim()
            if (!name || !label) {
              sendJson(res, 200, { code: 400, data: null, message: '角色标识与显示名不能为空' })
              return
            }
            if (MOCK_ROLES.some((r) => r.name === name)) {
              sendJson(res, 200, { code: 400, data: null, message: `角色 ${name} 已存在` })
              return
            }
            const newRole: MockRole = {
              id: Math.max(...MOCK_ROLES.map((r) => r.id)) + 1,
              name,
              label,
              description: String(body.description ?? '').trim(),
              permissions: [],
              dataScope: 'self',
              createdAt: new Date().toISOString().slice(0, 19).replace('T', ' '),
            }
            MOCK_ROLES.push(newRole)
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: newRole, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 分配角色权限：PUT /api/role/:id/permissions（全量覆盖，需先于「编辑角色」匹配）
        const permAssignMatch = /^\/api\/role\/(\d+)\/permissions$/.exec(url)
        if (req.method === 'PUT' && permAssignMatch) {
          const id = Number(permAssignMatch[1])
          const target = MOCK_ROLES.find((r) => r.id === id)
          if (!target) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '角色不存在' }), MOCK_DELAY)
            return
          }
          readBody(req).then((body) => {
            const permissions = Array.isArray(body.permissions)
              ? (body.permissions as unknown[]).filter((p): p is string => typeof p === 'string')
              : []
            target.permissions = permissions
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: target, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 编辑角色：PUT /api/role/:id（label/description 可改，name 不可改）
        const rolePutMatch = /^\/api\/role\/(\d+)$/.exec(url)
        if (req.method === 'PUT' && rolePutMatch) {
          const id = Number(rolePutMatch[1])
          const target = MOCK_ROLES.find((r) => r.id === id)
          if (!target) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '角色不存在' }), MOCK_DELAY)
            return
          }
          readBody(req).then((body) => {
            if ('label' in body) target.label = String(body.label ?? '').trim()
            if ('description' in body) target.description = String(body.description ?? '').trim()
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: target, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 删除角色：DELETE /api/role/:id（内置 admin 角色不可删除）
        const roleDeleteMatch = /^\/api\/role\/(\d+)$/.exec(url)
        if (req.method === 'DELETE' && roleDeleteMatch) {
          const id = Number(roleDeleteMatch[1])
          const idx = MOCK_ROLES.findIndex((r) => r.id === id)
          if (idx === -1) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '角色不存在' }), MOCK_DELAY)
            return
          }
          if (id === 1) {
            setTimeout(() => sendJson(res, 200, { code: 400, data: null, message: '内置管理员角色不可删除' }), MOCK_DELAY)
            return
          }
          MOCK_ROLES.splice(idx, 1)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: null, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 配置角色数据范围：PUT /api/role/:id/data-scope（需先于「编辑角色」匹配）
        const dataScopeMatch = /^\/api\/role\/(\d+)\/data-scope$/.exec(url)
        if (req.method === 'PUT' && dataScopeMatch) {
          const id = Number(dataScopeMatch[1])
          const target = MOCK_ROLES.find((r) => r.id === id)
          if (!target) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '角色不存在' }), MOCK_DELAY)
            return
          }
          readBody(req).then((body) => {
            const scope = body.dataScope
            if (scope !== 'all' && scope !== 'dept' && scope !== 'self') {
              sendJson(res, 200, { code: 400, data: null, message: '数据范围不合法' })
              return
            }
            target.dataScope = scope
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: target, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 菜单树：GET /api/menu/tree
        if (req.method === 'GET' && url === '/api/menu/tree') {
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: buildMenuTree(), message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 新增菜单：POST /api/menu（title/path 必填）
        if (req.method === 'POST' && url === '/api/menu') {
          readBody(req).then((body) => {
            const title = String(body.title ?? '').trim()
            const path = String(body.path ?? '').trim()
            if (!title || !path) {
              sendJson(res, 200, { code: 400, data: null, message: '菜单名与路径不能为空' })
              return
            }
            const parentId = Number(body.parentId ?? 0)
            if (parentId !== 0 && !MOCK_MENUS.some((m) => m.id === parentId)) {
              sendJson(res, 200, { code: 400, data: null, message: '父菜单不存在' })
              return
            }
            const newMenu = {
              id: Math.max(...MOCK_MENUS.map((m) => m.id)) + 1,
              parentId,
              title,
              path,
              icon: String(body.icon ?? '').trim(),
              authority: String(body.authority ?? '').trim(),
              order: Number(body.order ?? 0) || 0,
              hideInMenu: body.hideInMenu === true || body.hideInMenu === 'true',
            }
            MOCK_MENUS.push(newMenu)
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: newMenu, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 编辑菜单：PUT /api/menu/:id
        const menuPutMatch = /^\/api\/menu\/(\d+)$/.exec(url)
        if (req.method === 'PUT' && menuPutMatch) {
          const id = Number(menuPutMatch[1])
          const target = MOCK_MENUS.find((m) => m.id === id)
          if (!target) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '菜单不存在' }), MOCK_DELAY)
            return
          }
          readBody(req).then((body) => {
            if ('title' in body) target.title = String(body.title ?? '').trim()
            if ('path' in body) target.path = String(body.path ?? '').trim()
            if ('icon' in body) target.icon = String(body.icon ?? '').trim()
            if ('authority' in body) target.authority = String(body.authority ?? '').trim()
            if ('order' in body) target.order = Number(body.order ?? 0) || 0
            if ('hideInMenu' in body) target.hideInMenu = body.hideInMenu === true || body.hideInMenu === 'true'
            setTimeout(() => {
              sendJson(res, 200, { code: 200, data: target, message: 'success' })
            }, MOCK_DELAY)
          })
          return
        }

        // 删除菜单：DELETE /api/menu/:id（存在子菜单时拒绝）
        const menuDeleteMatch = /^\/api\/menu\/(\d+)$/.exec(url)
        if (req.method === 'DELETE' && menuDeleteMatch) {
          const id = Number(menuDeleteMatch[1])
          if (!MOCK_MENUS.some((m) => m.id === id)) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '菜单不存在' }), MOCK_DELAY)
            return
          }
          if (MOCK_MENUS.some((m) => m.parentId === id)) {
            setTimeout(() => sendJson(res, 200, { code: 400, data: null, message: '请先删除子菜单' }), MOCK_DELAY)
            return
          }
          MOCK_MENUS = MOCK_MENUS.filter((m) => m.id !== id)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: null, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 审计日志：GET /api/audit/logs?page=&pageSize=&operator=&action=&keyword=
        if (req.method === 'GET' && url.startsWith('/api/audit/logs')) {
          const searchParams = new URL(url, 'http://localhost').searchParams
          const page = Math.max(1, Number(searchParams.get('page') ?? 1))
          const pageSize = Math.max(1, Number(searchParams.get('pageSize') ?? 10))
          const operator = (searchParams.get('operator') ?? '').trim()
          const action = searchParams.get('action') ?? ''
          const keyword = (searchParams.get('keyword') ?? '').trim()
          const matched = MOCK_AUDIT_LOGS.filter((log) => {
            if (operator && !log.operator.includes(operator)) return false
            if (action && log.action !== action) return false
            if (keyword && !log.target.includes(keyword) && !log.detail.includes(keyword)) return false
            return true
          })
          const list = matched.slice((page - 1) * pageSize, page * pageSize)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: { list, total: matched.length }, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 通知列表：GET /api/notification/list?page=&pageSize=&type=&unreadOnly=
        if (req.method === 'GET' && url.startsWith('/api/notification/list')) {
          const searchParams = new URL(url, 'http://localhost').searchParams
          const page = Math.max(1, Number(searchParams.get('page') ?? 1))
          const pageSize = Math.max(1, Number(searchParams.get('pageSize') ?? 10))
          const type = searchParams.get('type') ?? ''
          const unreadOnly = searchParams.get('unreadOnly') === 'true'
          const matched = MOCK_NOTIFICATIONS.filter((n) => {
            if (type && n.type !== type) return false
            if (unreadOnly && n.read) return false
            return true
          })
          const list = matched.slice((page - 1) * pageSize, page * pageSize)
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: { list, total: matched.length }, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 未读计数：GET /api/notification/unread-count
        if (req.method === 'GET' && url === '/api/notification/unread-count') {
          const unread = MOCK_NOTIFICATIONS.filter((n) => !n.read).length
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: { unread }, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 单条已读：POST /api/notification/:id/read
        const notificationReadMatch = /^\/api\/notification\/(\d+)\/read$/.exec(url)
        if (req.method === 'POST' && notificationReadMatch) {
          const id = Number(notificationReadMatch[1])
          const target = MOCK_NOTIFICATIONS.find((n) => n.id === id)
          if (!target) {
            setTimeout(() => sendJson(res, 200, { code: 404, data: null, message: '通知不存在' }), MOCK_DELAY)
            return
          }
          target.read = true
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: null, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        // 全部已读：POST /api/notification/read-all
        if (req.method === 'POST' && url === '/api/notification/read-all') {
          MOCK_NOTIFICATIONS.forEach((n) => {
            n.read = true
          })
          setTimeout(() => {
            sendJson(res, 200, { code: 200, data: null, message: 'success' })
          }, MOCK_DELAY)
          return
        }

        next()
      })
    },
  }
}

/** 组件演示专用 Mock：拦截 /api/demo/* 与 /api/export/users，后端无此真实路由，dev 下始终启用（不随 VITE_MOCK_API 开关） */
export function mockDemoPlugin(): Plugin {
  return {
    name: 'yunshu-mock-demo',
    apply: 'serve', // 仅开发服务器生效
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? ''

        // 邮箱校验模拟接口（Demo 页失焦校验用，便于测试网络异常场景）
        // - 不含 @        → 业务错误（HTTP 200 + code 400，拦截器 toast + 页内提示）
        // - 含 network    → 模拟 HTTP 500（服务异常）
        // - 含 timeout    → 延迟 10s 不响应（超过前端 3s 超时，触发网络超时）
        // - slow 开头     → 延迟 3s（观察 Input 等待态）
        if (req.method === 'GET' && url.startsWith('/api/demo/validate-email')) {
          const searchParams = new URL(url, 'http://localhost').searchParams
          const email = (searchParams.get('email') ?? '').trim()
          let delay = MOCK_DELAY
          if (email.startsWith('slow')) delay = 3000
          if (email.includes('timeout')) delay = 10000 // 响应晚于前端超时时间，客户端以超时处理
          setTimeout(() => {
            if (email.includes('network')) {
              sendJson(res, 500, { code: 500, data: null, message: '服务器内部错误，请稍后重试' })
              return
            }
            if (!email.includes('@')) {
              sendJson(res, 200, { code: 400, data: null, message: '邮箱格式不正确' })
              return
            }
            sendJson(res, 200, { code: 200, data: { valid: true }, message: 'success' })
          }, delay)
          return
        }

        // 大数据量导出 Mock：GET /api/export/users（导出页在 VITE_EXPORT_LARGE_MOCK=true 时调用，
        // 本地验证 5000 条分片导出的进度条与性能；放 demo 插件保证不受 VITE_MOCK_API 开关影响）
        if (req.method === 'GET' && url === '/api/export/users') {
          setTimeout(() => {
            sendJson(res, 200, {
              code: 200,
              data: { list: EXPORT_MOCK_USERS, total: EXPORT_MOCK_USERS.length },
              message: 'success',
            })
          }, MOCK_DELAY)
          return
        }

        next()
      })
    },
  }
}
