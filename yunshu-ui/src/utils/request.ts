/**
 * Axios 请求封装（React 18 + TypeScript）
 * - 请求拦截：从 localStorage 读取 Token，注入 Authorization: Bearer <token>
 * - 响应拦截：
 *   1. 网络错误：按 HTTP status 处理（401 跳转登录 / 403 / 404 / 500 提示）
 *   2. 业务错误：response.data.code !== 200 时提示并 Reject
 *   3. 数据解包：成功直接返回 response.data.data
 * - 性能观测：每个请求前后记录耗时日志 [perf]，便于排查性能问题
 */
import axios, { type AxiosRequestConfig } from 'axios'
import { toast } from '@/components/Toaster'

/** 后端统一返回结构 */
export interface ApiResponse<T = unknown> {
  code: number
  data: T
  message: string
}

const TOKEN_KEY = 'token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

/** 【Why】日志脱敏：token 仅显示头尾，避免完整令牌落入日志（与路由守卫日志口径一致） */
function maskToken(token: string): string {
  return token.length > 8 ? `${token.slice(0, 8)}…${token.slice(-4)}` : '***'
}

/**
 * 全局提示：统一走 Tailwind Toast 组件（淡入淡出，见 src/components/Toaster.tsx）
 */
export function notify(message: string, level: 'error' | 'info' = 'error'): void {
  console.error(`[request] ${level}: ${message}`)
  if (level === 'info') {
    toast.info(message)
  } else {
    toast.error(message)
  }
}

/** 记录每个请求的起始时间（以 config 对象为 key，天然支持并发请求） */
const timingMap = new WeakMap<AxiosRequestConfig, number>()

/** 输出请求耗时日志 */
function logPerf(config: AxiosRequestConfig, status: number | string, costMs: number): void {
  const method = (config.method ?? 'GET').toUpperCase()
  console.info(`[perf] ${method} ${config.url ?? ''} ${status} ${costMs}ms`)
}

/** 创建 Axios 实例 */
const service = axios.create({
  // 【Why】用 || 而非 ??：.env 中 VITE_API_BASE 留空时得到空字符串（非 nullish），
  // ?? 不会回退导致 baseURL='' 拼出 /auth/login 缺 /api 前缀（Nginx 代理失效）。
  // || 对 空串/undefined 都回退 /api；Electron 注入绝对地址时不受影响。
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 15000,
})

// ---------- 请求拦截：计时 + 注入 Token ----------
service.interceptors.request.use((config) => {
  timingMap.set(config, performance.now())

  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ---------- 响应拦截：耗时日志 + 错误处理 + 数据解包 ----------
service.interceptors.response.use(
  (response) => {
    const cost = Math.round(performance.now() - (timingMap.get(response.config) ?? 0))
    logPerf(response.config, response.status, cost)

    // AxiosResponse.data 为 any，此处断言以获得字段类型提示
    const res = response.data as ApiResponse

    // 业务错误：code 非 200
    if (res.code !== 200) {
      notify(res.message || '业务处理失败')
      return Promise.reject(new Error(res.message || '业务处理失败'))
    }

    // 成功：解包，直接返回业务数据，减少调用方 .data 嵌套
    return res.data as never
  },
  async (error) => {
    const status: number | undefined = error.response?.status
    const cost = Math.round(performance.now() - (timingMap.get(error.config) ?? 0))
    logPerf(error.config, status ?? 'ERR', cost)

    switch (status) {
      case 401: {
        // 【Why】动态 import 延迟加载 userStore，避免静态循环依赖（userStore 依赖本模块 clearToken）；
        // 仅在拦截器回调运行时访问，不影响模块加载
        const { useUserStore } = await import('@/store/userStore')
        // 日志埋点：logout 会清空 token，须先取值；记录触发登出的凭证（脱敏）与调用堆栈，
        // 便于排查会话失效来源（过期 / 主动清除 / 权限回收等）
        const expiredToken = getToken() ?? useUserStore.getState().token
        console.info(
          `[auth] 401 触发 logout：token=${expiredToken ? maskToken(expiredToken) : null}，` +
            `调用堆栈：${new Error('401 logout').stack?.split('\n').slice(1, 4).join(' ← ')}`,
        )
        // 同步清理 store 与 localStorage：logout 内部 clearToken + 清空 userInfo，
        // 避免 401 后 store.token 残留（此前只清 localStorage 'token'，导致登录态不一致）
        useUserStore.getState().logout()
        // HashRouter 下路由位于 hash 中，直接改 hash 避免整页刷新；已在登录页时不重复跳转
        if (!window.location.hash.startsWith('#/login')) {
          window.location.hash = '#/login'
        }
        notify('登录已过期，请重新登录')
        break
      }
      case 403:
        notify('没有权限访问该资源')
        break
      case 404:
        notify('请求的资源不存在')
        break
      case 500:
        notify('服务器内部错误，请稍后重试')
        break
      default:
        notify(error.message || '网络异常，请稍后重试')
    }
    return Promise.reject(error)
  },
)

/**
 * 类型化请求方法：拦截器已解包，泛型 T 即为业务数据类型
 */
export function request<T = unknown>(config: AxiosRequestConfig): Promise<T> {
  // axios 1.19 的 request<T, R> 返回条件类型 AxiosResponseResult（R 为泛型时无法化简为 T），
  // 拦截器已解包 response.data.data，此处直接断言返回类型
  return service.request(config) as unknown as Promise<T>
}

export default request
