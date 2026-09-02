/**
 * ForbiddenPage —— 403 无权限页面
 * 原由管理后台 AuthRoute 守卫重定向进入；该外壳已摘除后保留为独立路由
 * （#/403 手输可访问），链接返回统一工作台。
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
