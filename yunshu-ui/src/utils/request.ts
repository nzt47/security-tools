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
import { STORAGE_KEYS, storage } from '@/utils/storage'

/** 后端统一返回结构 */
export interface ApiResponse<T = unknown> {
  code: number
  data: T
  message: string
}

// 【Why】token 读写统一走 storage 封装（getRaw/setRaw 原样字符串，保持既有契约键 'token' 不变）；
// 登录页 / axios 拦截器 / AdminGuard 直接读裸键，禁止改键名或引入 JSON 序列化。
export function getToken(): string | null {
  return storage.getRaw(STORAGE_KEYS.TOKEN)
}

export function setToken(token: string): void {
  storage.setRaw(STORAGE_KEYS.TOKEN, token)
}

export function clearToken(): void {
  storage.remove(STORAGE_KEYS.TOKEN)
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

/**
 * 记录每个请求的起始时间（以 config 对象为 key，天然支持并发请求）
 * 生产构建 perfEnabled 为编译期常量 false（import.meta.env.PROD 静态替换），
 * 计时与日志相关分支均被 esbuild 消除，生产环境零开销
 */
const timingMap = new WeakMap<AxiosRequestConfig, number>()

/** 是否启用请求耗时观测（生产关闭） */
const perfEnabled = !import.meta.env.PROD

/** 输出请求耗时日志（仅开发/测试打印） */
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
  // 仅开发/测试记录耗时（perfEnabled 生产为编译期 false，此分支不打包）
  if (perfEnabled) {
    timingMap.set(config, performance.now())
  }

  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ---------- 响应拦截：耗时日志 + 错误处理 + 数据解包 ----------
service.interceptors.response.use(
  (response) => {
    if (perfEnabled) {
      const cost = Math.round(performance.now() - (timingMap.get(response.config) ?? 0))
      logPerf(response.config, response.status, cost)
    }

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
    if (perfEnabled) {
      const cost = Math.round(performance.now() - (timingMap.get(error.config) ?? 0))
      logPerf(error.config, status ?? 'ERR', cost)
    }

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

/**
 * 请求取消管理：创建 AbortController 供"切换/卸载时取消在途请求"使用。
 * 用法：const { signal, cancel } = createRequestAbort();
 *       request({ url, method: 'GET', signal });  页面卸载时 cancel()
 * 配合 useTablePage 竞态防护（请求序号）使用时，二者可叠加（signal 取消为主动，序号丢弃为兜底）。
 */
export function createRequestAbort(): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController();
  return {
    signal: controller.signal,
    cancel: () => controller.abort(),
  };
}

export interface RetryOptions {
  /** 重试次数（不含首次），默认 2 */
  retries?: number;
  /** 重试间隔（ms），默认 500 */
  retryDelayMs?: number;
}

/** 等待重试间隔（内部辅助） */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/**
 * 带重试的请求（默认关闭）
 * - 由 VITE_REQUEST_RETRY_ENABLED（'true'/'1'）控制启用，默认关闭 → 直接单次请求
 * - 仅幂等请求（GET）生效；4xx 业务错误不重试（如 401 交拦截器登出，重试无意义）
 * - 网络错误/超时/5xx 按 retries 次重试，间隔 retryDelayMs
 */
export async function requestWithRetry<T = unknown>(
  config: AxiosRequestConfig,
  options?: RetryOptions,
): Promise<T> {
  const retries = options?.retries ?? 2;
  const retryDelayMs = options?.retryDelayMs ?? 500;
  const enabled =
    import.meta.env.VITE_REQUEST_RETRY_ENABLED === 'true' ||
    import.meta.env.VITE_REQUEST_RETRY_ENABLED === '1';
  if (!enabled || (config.method ?? 'GET').toUpperCase() !== 'GET') {
    return request<T>(config);
  }

  let lastErr: unknown;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await request<T>(config);
    } catch (err) {
      lastErr = err;
      // 4xx 业务错误不重试（错误提示已由拦截器统一处理）
      const status = (err as { response?: { status?: number } } | null)?.response?.status;
      if (status !== undefined && status >= 400 && status < 500) throw err;
      if (attempt < retries) await sleep(retryDelayMs);
    }
  }
  throw lastErr;
}

export default request
