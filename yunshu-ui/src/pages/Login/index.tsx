/**
 * LoginPage —— 登录页
 * 交互：调用 login 接口 → 成功写入 Token 并跳转；失败提示统一由全局 Toast 展示
 * （接口失败：axios 拦截器统一 toast；前端校验：页面直接调用 toast）
 */
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { login } from '@/api/user'
import { setToken as saveToken } from '@/utils/request'
import { useUserStore } from '@/store/userStore'
import { toast } from '@/components/Toaster'

/** 记住密码的 localStorage key */
const REMEMBER_KEY = 'yunshu-remember-login'

/** 已保存的登录凭证 */
interface RememberedCredentials {
  remember: boolean
  username: string
  password: string
}

/**
 * 读取已保存的登录凭证
 * 密码仅在勾选「记住密码」时回填；JSON 解析失败或缺失时返回空值
 */
function loadRememberedCredentials(): RememberedCredentials {
  try {
    const raw = localStorage.getItem(REMEMBER_KEY)
    if (!raw) return { remember: false, username: '', password: '' }
    const data = JSON.parse(raw) as Partial<RememberedCredentials>
    return {
      remember: !!data.remember,
      username: typeof data.username === 'string' ? data.username : '',
      password: data.remember && typeof data.password === 'string' ? data.password : '',
    }
  } catch {
    return { remember: false, username: '', password: '' }
  }
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  // 守卫重定向时携带的来源路径，登录后优先跳回
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname

  // 【Why】初始值来自 localStorage（记住密码），useState 惰性初始化只执行一次
  const [saved] = useState(loadRememberedCredentials)
  const [username, setUsername] = useState(saved.username)
  const [password, setPassword] = useState(saved.password)
  const [remember, setRemember] = useState(saved.remember)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (loading) return

    const name = username.trim()
    if (!name || !password) {
      // 前端校验不发请求，拦截器不参与，此处直接走全局 Toast
      toast.error('请输入用户名和密码')
      return
    }

    setLoading(true)
    try {
      const data = await login({ username: name, password })
      // 【Why】守卫与 axios 拦截器读 localStorage 'token'，必须写入；store 同步全局状态
      saveToken(data.token)
      useUserStore.getState().setToken(data.token)
      if (data.user) {
        useUserStore.getState().setUserInfo(data.user)
      }
      // 记住密码：勾选时保存凭证（含密码）；未勾选时清除，避免残留
      if (remember) {
        localStorage.setItem(
          REMEMBER_KEY,
          JSON.stringify({ remember: true, username: name, password })
        )
      } else {
        localStorage.removeItem(REMEMBER_KEY)
      }
      // 有来源路径跳回来源，否则直达仪表盘（/ 即路由表仪表盘，避免写死 /dashboard 触发兜底重定向）
      console.info(`[auth] 登录成功，已写入 token，跳转 → ${from ?? '/'}`)
      navigate(from ?? '/', { replace: true })
    } catch (err) {
      // 【Why】接口失败的错误提示已由全局 axios 拦截器统一 toast（业务/HTTP 错误均覆盖），
      // 页面不再重复展示，保持全站提示风格一致（单一来源，避免双弹）
      console.error(`[auth] 登录失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-xl">
      <h1 className="text-2xl font-bold text-slate-800">云枢</h1>
      <p className="mb-6 mt-1 text-sm text-slate-400">欢迎回来，请登录你的账号</p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="username" className="mb-1.5 block text-sm font-medium text-slate-600">
            用户名
          </label>
          <input
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="请输入用户名"
            autoComplete="username"
            className="w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
          />
        </div>

        <div>
          <label htmlFor="password" className="mb-1.5 block text-sm font-medium text-slate-600">
            密码
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="请输入密码"
            autoComplete="current-password"
            className="w-full rounded-lg border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
          />
        </div>

        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
          />
          记住密码
        </label>

        <button
          type="submit"
          disabled={loading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loading && (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
          )}
          {loading ? '登录中...' : '登录'}
        </button>
      </form>
    </div>
  )
}
