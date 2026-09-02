/**
 * 系统管理 —— 数据导出（复用管理后台 Export 页，嵌入 Hub 内容区）
 * ------------------------------------------------
 * 数据导出功能（CSV/JSON 分片下载）原挂在已摘除的管理后台路由 /export，
 * 现经本包装挂载到工作台「系统管理 → 数据导出」（hubNav admin/export）。
 */
import AdminGuard from './AdminGuard'
import DataExport from '@/pages/Export'

export default function HubAdminExport() {
  return (
    <AdminGuard>
      <div className="p-6">
        <DataExport />
      </div>
    </AdminGuard>
  )
}
