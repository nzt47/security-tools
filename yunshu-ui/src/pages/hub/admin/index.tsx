/**
 * 系统管理 —— 用户列表 / 角色权限 / 菜单管理 / 操作审计 / 消息中心 / 系统日志
 * 全部复用 develop 管理后台页面组件，嵌入 Hub 内容区。
 */
import AdminGuard from './AdminGuard'
import UserList from '@/pages/system/UserList'
import RoleList from '@/pages/system/RoleList'
import MenuList from '@/pages/system/MenuList'
import AuditList from '@/pages/system/AuditList'
import NotificationCenter from '@/pages/system/NotificationCenter'
import SystemLog from '@/pages/system/SystemLog'

export function HubAdminUsers() {
  return <AdminGuard><div className="p-6"><UserList /></div></AdminGuard>
}

export function HubAdminRoles() {
  return <AdminGuard><div className="p-6"><RoleList /></div></AdminGuard>
}

export function HubAdminMenus() {
  return <AdminGuard><div className="p-6"><MenuList /></div></AdminGuard>
}

export function HubAdminAudit() {
  return <AdminGuard><div className="p-6"><AuditList /></div></AdminGuard>
}

export function HubAdminNotifications() {
  return <AdminGuard><div className="p-6"><NotificationCenter /></div></AdminGuard>
}

export function HubAdminLogs() {
  return <AdminGuard><div className="p-6"><SystemLog /></div></AdminGuard>
}
