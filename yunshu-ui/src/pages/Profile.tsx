/**
 * 使用示例：封装后的 API 在 React 函数组件中的调用方式
 * - useEffect 加载用户信息（含卸载防竞态）
 * - 事件处理中调用登录接口
 * - 统一的 loading 状态驱动按钮禁用与加载文案
 */
import { useEffect, useState } from 'react'
import { getUserInfo, login } from '@/api/user'
import type { LoginParams, UserInfo } from '@/api/user'
import { clearToken, setToken } from '@/utils/request'

export default function Profile() {
  // 演示页表单不预填默认密码（避免硬编码测试凭证；由用户手动输入）
  const [form, setForm] = useState<LoginParams>({ username: '', password: '' })
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null)
  const [loading, setLoading] = useState(false)

  // 页面加载时拉取用户信息（组件卸载后禁止 setState，避免竞态告警）
  useEffect(() => {
    let cancelled = false

    async function fetchUser() {
      setLoading(true)
      try {
        const data = await getUserInfo()
        if (!cancelled) setUserInfo(data)
      } catch {
        if (!cancelled) setUserInfo(null) // 未登录：错误提示由拦截器处理
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchUser()
    return () => {
      cancelled = true
    }
  }, [])

  // 事件处理：登录（成功后顺带刷新用户信息）
  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      const { token } = await login(form)
      setToken(token) // 写入 Token，后续请求自动携带
      const data = await getUserInfo()
      setUserInfo(data)
    } catch {
      // 错误提示已由响应拦截器统一处理（alert + console）
    } finally {
      setLoading(false)
    }
  }

  function handleLogout() {
    clearToken()
    setUserInfo(null)
  }

  return (
    <div className="mx-auto max-w-md p-6">
      <h1 className="mb-4 text-xl font-semibold">登录测试</h1>

      <form onSubmit={handleLogin} className="space-y-3">
        <input
          value={form.username}
          onChange={(e) => setForm({ ...form, username: e.target.value })}
          placeholder="用户名"
          className="w-full rounded border border-gray-300 px-3 py-2"
        />
        <input
          type="password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          placeholder="密码"
          className="w-full rounded border border-gray-300 px-3 py-2"
        />
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded bg-blue-500 px-4 py-2 text-white disabled:opacity-50"
        >
          {loading ? '登录中...' : '登录'}
        </button>
      </form>

      <div className="mt-6">
        {loading ? (
          <p className="text-gray-500">加载中...</p>
        ) : userInfo ? (
          <div className="rounded border border-green-200 bg-green-50 p-4">
            <p>欢迎，{userInfo.nickname}（{userInfo.username}）</p>
            <p className="mt-1 text-sm text-gray-500">{userInfo.email}</p>
            <button
              type="button"
              onClick={handleLogout}
              className="mt-3 rounded border border-gray-300 px-3 py-1 text-sm"
            >
              退出登录
            </button>
          </div>
        ) : (
          <p className="text-gray-400">未登录，请先登录</p>
        )}
      </div>
    </div>
  )
}
