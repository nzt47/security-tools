/**
 * SystemLog —— 系统管理 / 系统日志（示例页面）
 * 权限：system:view（普通用户可见），用于验证「系统管理分组下部分菜单对 user 开放」。
 */
import { ScrollText } from 'lucide-react'

export default function SystemLog() {
  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-2 text-2xl font-semibold text-slate-800">系统日志</h1>
      <p className="mb-6 text-sm text-slate-500">
        示例页面：路由 /system/log（权限码 system:view，普通用户可见），
        用于验证「系统管理分组下部分菜单对 user 开放」。
      </p>

      <div className="flex flex-col items-center gap-3 rounded-lg border border-slate-200 bg-white py-16 text-slate-400">
        <ScrollText size={32} />
        <p className="text-sm">暂无日志数据（示例占位页）</p>
      </div>
    </div>
  )
}
