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
import HubChannelsPanel from './messages-channels'

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

/**
 * 消息中心 = 通知列表（NotificationCenter）+ 消息通道（HubChannelsPanel）。
 *
 * 【2026-10-18】通道（webhook/SMTP/Slack/Discord/telegram）从「扩展市场」并入此处：
 * 它是**消息发送**通道，语义在消息中心；不并入「网络配置」（那是代理/网络环境）。
 * 该组标注了「当前无发送调用方（未接线）」—— 依据是 channels/send 在仓库内无产品调用方。
 */
export function HubAdminNotifications() {
  return (
    <AdminGuard>
      <div className="p-6">
        <NotificationCenter />
        <HubChannelsPanel />
      </div>
    </AdminGuard>
  )
}

export function HubAdminLogs() {
  return <AdminGuard><div className="p-6"><SystemLog /></div></AdminGuard>
}
