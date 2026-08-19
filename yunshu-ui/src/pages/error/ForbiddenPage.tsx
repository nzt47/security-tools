/**
 * ForbiddenPage —— 403 无权限页面
 * 路由守卫（AuthRoute）检测到当前用户无权访问时重定向到此页。
 */
import { Link } from 'react-router-dom'

export default function ForbiddenPage() {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-slate-50">
      <span className="text-6xl font-bold text-slate-300">403</span>
      <p className="text-sm text-slate-500">抱歉，你无权访问该页面</p>
      <Link
        to="/"
        className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700"
      >
        返回仪表盘
      </Link>
    </div>
  )
}
