/**
 * DataExport —— 数据导出页
 * ------------------------------------------------
 * 功能：拉取数据集 → 表格预览 → 选择格式（CSV / JSON）→ 下载导出文件
 * 合规（Electron 迁移审查标准）：
 *   - 路由：挂在 HashRouter（src/router/routes.tsx 配置树，随 MainLayout 受保护）
 *   - 下载：统一走 src/utils/system.ts 的 downloadFile（迁移时只需替换该函数）
 *   - 接口：经 src/utils/request.ts，baseURL 读取 VITE_API_BASE，无硬编码地址
 * 日志：数据拉取与文件下载前后均打印 [export] / [download] 埋点，便于排查。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FileDown, FileJson, FileSpreadsheet, Loader2, RefreshCw, X } from 'lucide-react'
import { getExportMockUsers, getUserList } from '@/api/user'
import type { UserListItem } from '@/api/user'
import { downloadFile } from '@/utils/system'

type ExportFormat = 'csv' | 'json'

/** 大数据量 Mock 开关：.env.development 的 VITE_EXPORT_LARGE_MOCK=true 时走 5000 条演示数据 */
const USE_LARGE_MOCK = import.meta.env.VITE_EXPORT_LARGE_MOCK === 'true'

/** 导出文件名时间戳（与 PromptLab 同规则：去除冒号/句点，避免文件系统不兼容） */
const dateTag = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)

/** 分片大小：每批处理 2000 条，批次间让出主线程，避免大数据量一次同步卡顿 */
const CSV_BATCH = 2000

/** CSV 表头（与表格列一致） */
export const CSV_HEADER = ['ID', '用户名', '邮箱', '角色', '状态', '创建时间'].join(',')

/** 单行 CSV 转义拼接（供 buildCsv 与分片导出共用，保证格式一致） */
export function toCsvRow(u: UserListItem): string {
  return [u.id, u.username, u.email, u.role, u.status === 1 ? '启用' : '禁用', u.createdAt]
    .map(csvCell)
    .join(',')
}

/** CSV 单元格转义：含逗号/引号/换行时加引号包裹，内部引号翻倍 */
export function csvCell(value: string | number): string {
  const s = String(value)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** 用户列表 → CSV 文本（含表头；一次性构建，小数据量/测试用，大数据量走 handleExport 分片路径） */
export function buildCsv(users: UserListItem[]): string {
  return [CSV_HEADER, ...users.map(toCsvRow)].join('\n')
}

export default function DataExport() {
  const [users, setUsers] = useState<UserListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [format, setFormat] = useState<ExportFormat>('csv')
  const [exporting, setExporting] = useState(false)
  /** 导出进度（已处理条数），导出期间驱动进度条与按钮文案 */
  const [progress, setProgress] = useState(0)
  /** 取消导出标志：分片循环每批开头检查，点击「取消」后置位，在下一个分片边界中断 */
  const cancelRef = useRef(false)

  /** 拉取数据（挂载 + 手动刷新共用；大数据 Mock 与常规接口二选一） */
  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    const sourceDesc = USE_LARGE_MOCK ? '大数据 Mock（/api/export/users，5000 条）' : '/api/user/list'
    console.info(`[export] 开始拉取数据源：${sourceDesc}`)
    try {
      const { list } = USE_LARGE_MOCK
        ? await getExportMockUsers()
        : await getUserList({ page: 1, pageSize: 100 })
      setUsers(list)
      console.info(`[export] 数据拉取成功：共 ${list.length} 条`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '数据拉取失败')
      console.error(`[export] 数据拉取失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData()
  }, [loadData])

  /** 让出主线程：批次间给渲染/交互留出时间片（大数据量导出的防卡顿核心） */
  const yieldToMain = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

  /** 分片循环中检查取消标志：已取消则中断并记录日志（调用方 finally 负责清理状态） */
  const checkCancel = (i: number, total: number): boolean => {
    if (!cancelRef.current) return false
    console.info(`[export] 用户取消导出：已在分片边界中断（已处理 ${i}/${total}），跳过下载`)
    return true
  }

  /**
   * 导出（分片异步版，支持大数据量）：
   * 1. 按 CSV_BATCH 分片生成，批次间让出主线程并更新进度，避免一次同步序列化卡住 UI
   * 2. CSV 前置 BOM（\ufeff，Excel 正确识别 UTF-8 中文）；JSON 使用紧凑格式（无缩进）
   * 3. 每步打印 [export] 日志（分片 / 拼接 / 下载触发 + 累计耗时），便于排查大数据量性能
   * 4. 用户可中途取消：cancelRef 置位后在下一个分片边界中断，状态由 finally 统一清理
   */
  const handleExport = async () => {
    if (users.length === 0 || exporting) return
    const total = users.length
    const tag = dateTag()
    const t0 = performance.now()
    cancelRef.current = false
    setExporting(true)
    setProgress(0)
    console.info(`[export] 开始导出：${format.toUpperCase()}，共 ${total} 条，分片大小 ${CSV_BATCH}`)

    try {
      if (format === 'csv') {
        // 分片生成 CSV 行；表头（含 BOM）与每片行文本作为独立 Blob 分片交付，
        // 由 Blob 内部拼接，避免在 JS 侧拼出整份大字符串（大数据量峰值内存减半）
        const parts: string[] = [`\ufeff${CSV_HEADER}`]
        for (let i = 0; i < total; i += CSV_BATCH) {
          if (checkCancel(i, total)) return
          const end = Math.min(i + CSV_BATCH, total)
          parts.push('\n', users.slice(i, end).map(toCsvRow).join('\n'))
          setProgress(end)
          console.info(`[export] CSV 分片 [${i}, ${end})/${total} 已生成（累计 ${Math.round(performance.now() - t0)}ms）`)
          await yieldToMain()
        }
        if (checkCancel(total, total)) return
        console.info(`[export] CSV 分片全部生成：${parts.length} 片，直接交付 Blob（无中间大字符串）`)
        downloadFile(`yunshu-users-${tag}.csv`, parts, 'text/csv;charset=utf-8')
      } else {
        // 分片序列化 JSON（紧凑格式）：去掉每片首尾方括号，整数组括号由首尾分片补齐
        const parts: string[] = ['[']
        for (let i = 0; i < total; i += CSV_BATCH) {
          if (checkCancel(i, total)) return
          const end = Math.min(i + CSV_BATCH, total)
          parts.push(JSON.stringify(users.slice(i, end)).slice(1, -1))
          setProgress(end)
          console.info(`[export] JSON 分片 [${i}, ${end})/${total} 已序列化（累计 ${Math.round(performance.now() - t0)}ms）`)
          await yieldToMain()
        }
        if (checkCancel(total, total)) return
        parts.push(']')
        console.info(`[export] JSON 分片全部生成：${parts.length} 片，直接交付 Blob（无中间大字符串）`)
        downloadFile(`yunshu-users-${tag}.json`, parts, 'application/json')
      }
      console.info(`[export] 下载已触发：${format.toUpperCase()}，总耗时 ${Math.round(performance.now() - t0)}ms`)
    } catch (err) {
      console.error(`[export] 导出失败：${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setExporting(false)
      setProgress(0)
    }
  }

  /** 取消导出：置位取消标志，分片循环在下一个分片边界中断 */
  const handleCancel = () => {
    cancelRef.current = true
    console.info('[export] 用户点击取消')
  }

  const stats = useMemo(
    () => ({
      total: users.length,
      enabled: users.filter((u) => u.status === 1).length,
      admins: users.filter((u) => u.role === 'admin').length,
    }),
    [users],
  )

  return (
    <div className="space-y-4">
      {/* 顶部：标题 + 数据统计 + 操作 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">数据导出</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            共 {stats.total} 条 · 启用 {stats.enabled} · 管理员 {stats.admins}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void loadData()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting || users.length === 0}
            className="flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exporting ? <Loader2 size={14} className="animate-spin" /> : <FileDown size={14} />}
            {exporting
              ? `导出中 ${progress}/${users.length}`
              : `导出 ${format === 'csv' ? 'CSV' : 'JSON'}`}
          </button>
          {exporting && (
            <button
              type="button"
              onClick={handleCancel}
              className="flex items-center gap-1.5 rounded-md border border-red-200 bg-white px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50"
            >
              <X size={14} />
              取消
            </button>
          )}
        </div>
      </div>

      {/* 格式选择 */}
      <div className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white px-4 py-3">
        <span className="text-sm text-slate-500">导出格式</span>
        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-700">
          <input
            type="radio"
            name="format"
            value="csv"
            checked={format === 'csv'}
            onChange={() => setFormat('csv')}
            className="accent-indigo-600"
          />
          <FileSpreadsheet size={14} className="text-green-600" />
          CSV（表格软件可直接打开）
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-slate-700">
          <input
            type="radio"
            name="format"
            value="json"
            checked={format === 'json'}
            onChange={() => setFormat('json')}
            className="accent-indigo-600"
          />
          <FileJson size={14} className="text-amber-600" />
          JSON（保留完整字段结构）
        </label>
      </div>

      {/* 导出进度：分片生成期间展示进度条与已处理条数 */}
      {exporting && (
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>正在生成导出内容（分片处理中，不阻塞页面）...</span>
            <span>
              {progress}/{users.length} 条
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-slate-100">
            <div
              className="h-full rounded bg-indigo-500 transition-all duration-200"
              style={{ width: `${users.length ? (progress / users.length) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {/* 数据预览表 */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        {error ? (
          <div className="px-4 py-8 text-center text-sm text-red-600">{error}</div>
        ) : loading && users.length === 0 ? (
          <div className="flex items-center justify-center gap-2 px-4 py-8 text-sm text-slate-400">
            <Loader2 size={16} className="animate-spin" />
            数据加载中...
          </div>
        ) : users.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-400">暂无数据，请点击「刷新」重试</div>
        ) : (
          <div className="max-h-[60vh] overflow-auto">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2.5 font-medium">ID</th>
                  <th className="px-4 py-2.5 font-medium">用户名</th>
                  <th className="px-4 py-2.5 font-medium">邮箱</th>
                  <th className="px-4 py-2.5 font-medium">角色</th>
                  <th className="px-4 py-2.5 font-medium">状态</th>
                  <th className="px-4 py-2.5 font-medium">创建时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2 text-slate-500">{u.id}</td>
                    <td className="px-4 py-2 text-slate-800">{u.username}</td>
                    <td className="px-4 py-2 text-slate-500">{u.email}</td>
                    <td className="px-4 py-2">
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{u.role}</span>
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${
                          u.status === 1 ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'
                        }`}
                      >
                        {u.status === 1 ? '启用' : '禁用'}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-slate-500">{u.createdAt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
