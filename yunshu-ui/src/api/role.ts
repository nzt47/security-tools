/**
 * 角色与权限管理接口（RBAC）
 * 契约：标准 REST（/api/role*、/api/permissions）。
 * 说明：当前真实后端尚未实现本组接口，devMock（src/mocks/devMock.ts）提供同构 mock；
 *       后端就绪后仅需对齐字段与 URL 即可无缝切换。
 */
import request from '@/utils/request'

/** 权限码项 */
export interface PermissionItem {
  /** 权限码（如 'system:user:view'），与 routes.tsx 的 authority 对应 */
  code: string
  /** 显示名（如 用户查看） */
  label: string
  /** 分组（如 系统管理），用于界面分组展示 */
  group: string
}

/** 角色项 */
export interface RoleItem {
  id: number
  /** 角色标识（如 admin / manager / user），与 userInfo.role 对应 */
  name: string
  /** 显示名（如 管理员） */
  label: string
  description?: string
  /** 已分配权限码列表 */
  permissions: string[]
  /** 数据范围（M3：all 全部 / dept 本部门 / self 仅本人），未配置时为 undefined */
  dataScope?: 'all' | 'dept' | 'self'
  createdAt: string
}

/** 角色列表查询入参（分页 + 搜索） */
export interface RoleListParams {
  page: number
  pageSize: number
  /** 角色名/显示名关键字（可选） */
  keyword?: string
}

/** 角色列表分页返回 */
export interface RoleListResult {
  list: RoleItem[]
  total: number
}

/** 新增/编辑角色入参（name 唯一且不可改） */
export interface RoleFormParams {
  name: string
  label: string
  description?: string
}

/** 获取角色列表（分页 + 搜索） */
export function getRoleList(params: RoleListParams): Promise<RoleListResult> {
  return request<RoleListResult>({
    url: '/role/list',
    method: 'GET',
    params,
  })
}

/** 新增角色 */
export function createRole(data: RoleFormParams): Promise<RoleItem> {
  return request<RoleItem>({
    url: '/role',
    method: 'POST',
    data,
  })
}

/** 编辑角色（name 不可改，仅 label/description） */
export function updateRole(id: number, data: RoleFormParams): Promise<RoleItem> {
  return request<RoleItem>({
    url: `/role/${id}`,
    method: 'PUT',
    data,
  })
}

/** 删除角色（有用户引用的角色由后端拒绝） */
export function deleteRole(id: number): Promise<null> {
  return request<null>({
    url: `/role/${id}`,
    method: 'DELETE',
  })
}

/** 获取权限码列表（分组，用于权限分配界面） */
export function getPermissionList(): Promise<PermissionItem[]> {
  return request<PermissionItem[]>({
    url: '/permissions',
    method: 'GET',
  })
}

/** 分配角色权限（全量覆盖：最终权限 = 提交的权限码集合） */
export function assignRolePermissions(id: number, permissions: string[]): Promise<RoleItem> {
  return request<RoleItem>({
    url: `/role/${id}/permissions`,
    method: 'PUT',
    data: { permissions },
  })
}
