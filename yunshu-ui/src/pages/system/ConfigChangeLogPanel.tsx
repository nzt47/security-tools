/**
 * ConfigChangeLogPanel —— 系统管理 · 系统日志页的「配置变更日志」区
 * ------------------------------------------------------------------
 * 数据源：GET /api/config/logs?limit=N
 *   后端 agent/server_routes/routes_config.py → ncm.get_change_log(limit)
 *   返回 { ok: boolean, logs: [{ id, timestamp, action, section, details }] }
 *
 * 【为什么加在这里】《工作台 UI 信息架构评审》§7：把「该进 UI 却未进」的后端路由分批接进来。
 * 本面板是该批次第一条（系统管理面、只读日志、无写操作）。
 * 【失败必须响】读取失败上屏；空库显示空态，不伪装成成功。
 */
import { useCallback, useEffect, useState } from 'react'
import { History, RefreshCw } from 'lucide-react'
import { request } from '@/lib/apiClient'
import { CONFIG_CHANGE_LOGS } from '@/api/endpoints'

interface ChangeLogRow {
  id?: string
  timestamp?: string
  action?: string
  section?: string
  details?: Record<string, unknown>
}

const ACTION_LABEL: Record<string, string> = { create: '新增', update: '修改', delete: '删除' }

function fmtTime(ts?: string): string {
  if (!ts) return '—'
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString()
}

export default function ConfigChangeLogPanel() {
  const [rows, setRows] = useState<ChangeLogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    request<{ ok?: boolean; logs?: ChangeLogRow[]; error?: string }>(`${CONFIG_CHANGE_LOGS}?limit=20`)
      .then((d) => {
        if (d && d.ok === false) {
          setError(d.error || '后端返回 ok=false')
          return
        }
        setRows(Array.isArray(d?.logs) ? d.logs : [])
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <section className="mt-6" data-testid="config-change-log">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-800">
          <History size={16} /> 配置变更日志
        </h2>
        <button
          type="button"
          onClick={load}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
        >
          <RefreshCw size={12} /> 刷新
        </button>
      </div>
      <p className="mb-3 text-xs text-slate-500">
        数据源 GET /api/config/logs（NetworkConfig.change_log，最多保留最近 100 条）。
      </p>

      {error && (
        <div role="alert" data-testid="config-change-log-error"
          className="mb-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-600">
          读取失败：{error}
        </div>
      )}
      {loading && <div className="text-sm text-slate-500">加载中…</div>}

      {!loading && !error && rows.length === 0 && (
        <div data-testid="config-change-log-empty"
          className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
          暂无配置变更记录（后端 change_log 为空）。
        </div>
      )}

      {rows.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm" data-testid="config-change-log-table">
            <thead className="bg-slate-50">
              <tr>
                {['时间', '动作', '对象', '详情'].map((h) => (
                  <th key={h} className="px-4 py-2.5 text-left font-medium text-slate-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r, i) => (
                <tr key={r.id || i} className="transition hover:bg-slate-50">
                  <td className="px-4 py-2.5 text-slate-500">{fmtTime(r.timestamp)}</td>
                  <td className="px-4 py-2.5 font-medium text-slate-700">{ACTION_LABEL[r.action || ''] ?? r.action ?? '—'}</td>
                  <td className="px-4 py-2.5 text-slate-600">{r.section ?? '—'}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-slate-500">
                    {r.details ? JSON.stringify(r.details) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
