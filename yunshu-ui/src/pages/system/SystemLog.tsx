/**
 * SystemLog —— 系统管理 / 系统日志
 * - 页面可见性由后端菜单树决定（system:view 下发）
 * - 导出按钮为操作级权限：admin 持有 system:log:export（usePermission 判定），
 *   manager/user 无该权限码 → 按钮隐藏
 * - 数据为本地 mock（最小落地版）：导出将表格导出为 CSV 文件，无需后端接口
 */
import { Download, ScrollText } from 'lucide-react'
import { usePermission } from '@/hooks/usePermission'

/** 日志行（本地 mock 数据，供导出演示） */
interface LogRow {
  time: string
  operator: string
  action: string
  result: string
}

const MOCK_LOGS: LogRow[] = [
  { time: '2026-08-21 10:02:11', operator: 'admin', action: '登录', result: '成功' },
  { time: '2026-08-21 10:05:47', operator: 'manager', action: '导出用户数据', result: '成功' },
  { time: '2026-08-21 10:12:03', operator: 'admin', action: '修改角色权限', result: '成功' },
  { time: '2026-08-21 10:24:55', operator: 'user', action: '登录', result: '成功' },
  { time: '2026-08-21 10:33:19', operator: 'user', action: '修改个人信息', result: '成功' },
  { time: '2026-08-21 10:41:02', operator: 'admin', action: '删除用户 user12', result: '成功' },
]

export default function SystemLog() {
  // 操作级权限：system:log:export（admin 通配持有，manager/user 无 → 按钮隐藏）
  const canExport = usePermission('system:log:export')

  /** 导出：将 mock 日志导出为 CSV（带 BOM，Excel 打开中文不乱码） */
  function handleExport() {
    const header = ['时间', '操作人', '操作', '结果'].join(',')
    const rows = MOCK_LOGS.map((r) => [r.time, r.operator, r.action, r.result].join(','))
    const blob = new Blob([`\uFEFF${[header, ...rows].join('\n')}`], {
      type: 'text/csv;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `system-log-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-2 flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-800">系统日志</h1>
        {/* 操作级权限控制：无 system:log:export 权限码不渲染导出按钮 */}
        {canExport && (
          <button
            type="button"
            onClick={handleExport}
            className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500"
          >
            <Download size={14} />
            导出日志
          </button>
        )}
      </div>
      <p className="mb-6 text-sm text-slate-500">
        示例页面：路由 /system/log（权限码 system:view，可见性由后端菜单树 /api/auth/menus 下发控制）。
      </p>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              {['时间', '操作人', '操作', '结果'].map((head) => (
                <th key={head} className="px-4 py-2.5 text-left font-medium text-slate-500">
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {MOCK_LOGS.map((row, index) => (
              <tr key={index} className="transition hover:bg-slate-50">
                <td className="px-4 py-2.5 text-slate-500">{row.time}</td>
                <td className="px-4 py-2.5 font-medium text-slate-700">{row.operator}</td>
                <td className="px-4 py-2.5 text-slate-600">{row.action}</td>
                <td className="px-4 py-2.5">
                  <span className="rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-600">
                    {row.result}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center gap-2 text-xs text-slate-400">
        <ScrollText size={14} />
        <span>数据为本地 mock，共 {MOCK_LOGS.length} 条；导出为 CSV（仅 admin 可见导出按钮）</span>
      </div>
    </div>
  )
}
