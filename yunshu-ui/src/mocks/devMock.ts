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

/** 管理员账号（admin/123456）：角色 admin，可见全部菜单（含系统管理） */
const MOCK_ADMIN: MockUser = {
  id: 1,
  username: 'admin',
  nickname: '本地管理员',
  email: 'admin@yunshu.local',
  avatar: MOCK_AVATAR,
  role: 'admin',
  permissions: ['dashboard:view', 'workbench:use', 'prompt-lab:use'],
}

/** 普通用户账号（user/123456）：角色 user，拥有 system:view（可见系统管理分组/系统日志），
 *  无 system:user:view（用户列表对其隐藏），用于验证「部分菜单开放 + 403 跳转」 */
const MOCK_USER: MockUser = {
  id: 2,
  username: 'user',
  nickname: '普通用户',
  email: 'user@yunshu.local',
  avatar: MOCK_AVATAR,
  role: 'user',
  permissions: ['dashboard:view', 'workbench:use', 'system:view'],
}

/** 按用户名取 mock 用户（login 已校验账号，此处不会遇到未知用户名） */
function getUserByUsername(username: string): MockUser {
  return username === 'user' ? MOCK_USER : MOCK_ADMIN
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
