/**
 * 系统管理 —— 仪表盘（复用 develop 管理后台页面）
 */
import AdminGuard from './AdminGuard'
import Dashboard from '@/pages/Dashboard'

export default function HubAdminDashboard() {
  return (
    <AdminGuard>
      <div className="p-6">
        <Dashboard />
      </div>
    </AdminGuard>
  )
}
