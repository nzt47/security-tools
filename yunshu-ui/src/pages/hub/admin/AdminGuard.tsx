/**
 * 管理后台页面 Hub 包装
 * ------------------------------------------------
 * 复用 develop 分支的管理后台页面（UserList/RoleList/…），嵌入 Hub 内容区。
 * 这些页面通过 localStorage['token']（utils/request 拦截器）自动注入
 * Authorization，与 /login 登录态共享；未登录时列表接口返回 401，
 * 本包装提供登录提示与跳转。
 */
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Lock } from 'lucide-react'

export function getHubToken(): string | null {
  try {
    return localStorage.getItem('token')
  } catch {
    return null
  }
}

/** 登录守卫包装：未登录时显示提示条 */
export default function AdminGuard({ children }: { children: ReactNode }) {
  const [token] = useState(getHubToken)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    setChecked(true)
  }, [])

  return (
    <div>
      {!token && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-amber-800/60 bg-amber-950/40 px-4 py-3">
          <div className="flex items-center gap-2 text-sm text-amber-300">
            <Lock size={14} />
            <span>管理后台需要登录：<code className="rounded bg-amber-900/40 px-1.5 py-0.5 text-xs">admin / 123456</code></span>
          </div>
          <Link
            to="/login"
            className="rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-amber-500"
          >
            去登录
          </Link>
        </div>
      )}
      {checked && children}
    </div>
  )
}
