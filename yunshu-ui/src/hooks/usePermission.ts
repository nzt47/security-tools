/**
 * usePermission —— 操作级权限判定 hook（统一封装）
 * ------------------------------------------------------
 * 判定语义与后端 PermissionManager.has_permission 完全一致：
 *   - 空权限码 → 公开（true）
 *   - admin 角色 → 通配（true）
 *   - 其余角色 → 命中 userInfo.permissions 集合
 *
 * 用法（按钮级权限控制）：
 *   const canExport = usePermission('system:log:export')
 *   {canExport && <button onClick={handleExport}>导出</button>}
 *
 * 【Why 不依赖 router/routes】：SystemLog → usePermission → routes → SystemLog
 * 会形成循环依赖，模块求值时组件尚未导出（routes.tsx 中 <SystemLog/> 为 undefined，
 * React 报 "type is invalid" 警告）。此处内联判定（3 行），逻辑与 hasAuthority 一致。
 */
import { useUserStore } from '@/store/userStore'

export function usePermission(code: string): boolean {
  const userInfo = useUserStore((s) => s.userInfo)
  if (!code) return true
  if (userInfo?.role === 'admin') return true
  return userInfo?.permissions?.includes(code) ?? false
}
