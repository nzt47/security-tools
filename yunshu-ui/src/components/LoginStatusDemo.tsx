/**
 * LoginStatusDemo —— userStore 使用示例
 * ------------------------------------------------------
 * 演示三件事：
 * 1. 通过选择器从 store 获取 token / userInfo（token 变化时组件自动重渲染）
 * 2. 展示登录状态（已登录 / 未登录）
 * 3. 监听 token：变为空（登出 / 会话失效）时自动跳转登录页
 *
 * 说明：本项目 router/index.tsx 已有全局登录守卫 RequireAuth，
 * 组件内监听跳转适用于局部页面（如独立窗口 / 弹出面板）。
 */
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useUserStore } from '@/store/userStore'

export default function LoginStatusDemo() {
  const navigate = useNavigate()
  // 【Why】选择器即响应式订阅：token 变化时本组件自动重渲染，无需手动 subscribe
  const token = useUserStore((s) => s.token)
  const userInfo = useUserStore((s) => s.userInfo)
  const logout = useUserStore((s) => s.logout)

  // 监听 token：为空（登出 / 过期清除）时自动跳登录页
  useEffect(() => {
    if (!token) {
      navigate('/login', { replace: true })
    }
  }, [token, navigate])

  // 已触发跳转，避免先渲染一次未登录态造成闪烁
  if (!token) return null

  // token 脱敏展示：仅显示头尾，避免完整令牌泄露到页面
  const maskedToken = `${token.slice(0, 8)}…${token.slice(-4)}`
  const displayName = userInfo?.nickname || userInfo?.username || '未知用户'

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
        <span className="text-sm font-medium text-slate-800">已登录</span>
      </div>

      <dl className="mt-3 space-y-2 text-sm text-slate-600">
        <div className="flex gap-2">
          <dt className="w-16 shrink-0 text-slate-400">用户名</dt>
          <dd>{displayName}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-16 shrink-0 text-slate-400">Token</dt>
          <dd>
            <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{maskedToken}</code>
          </dd>
        </div>
      </dl>

      <button
        type="button"
        onClick={logout}
        className="mt-4 rounded-md bg-slate-800 px-3 py-1.5 text-sm text-white transition-colors hover:bg-slate-700"
      >
        退出登录
      </button>
    </div>
  )
}
