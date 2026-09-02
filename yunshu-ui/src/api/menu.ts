/**
 * 菜单与数据权限管理接口
 * 契约：标准 REST（/api/menu*、/api/role/:id/data-scope）。
 * 说明：真实后端尚未实现本组接口，devMock（src/mocks/devMock.ts）提供同构 mock。
 */
import request from '@/utils/request'
import type { RoleItem } from './role'

/** 菜单项（树形） */
export interface MenuItem {
  id: number
  /** 父菜单 id，0 表示顶级 */
  parentId: number
  /** 菜单名 */
  title: string
  /** 路由路径（如 /system/user） */
  path: string
  /** 图标名（lucide 图标名，如 LayoutDashboard） */
  icon?: string
  /** 权限码（如 system:user:view），空表示登录用户可见 */
  authority?: string
  /** 排序值（越小越靠前） */
  order: number
  /** 是否在菜单隐藏 */
  hideInMenu: boolean
  children?: MenuItem[]
}

/** 菜单表单入参（新增/编辑） */
export interface MenuFormParams {
  parentId: number
  title: string
  path: string
  icon?: string
  authority?: string
  order: number
  hideInMenu: boolean
}

/** 数据范围（all 全部 / dept 本部门 / self 仅本人） */
export type DataScope = 'all' | 'dept' | 'self'

/** 获取菜单树 */
export function getMenuTree(): Promise<MenuItem[]> {
  return request<MenuItem[]>({
    url: '/menu/tree',
    method: 'GET',
  })
}

/** 新增菜单（可指定 parentId 作为子菜单） */
export function createMenu(data: MenuFormParams): Promise<MenuItem> {
  return request<MenuItem>({
    url: '/menu',
    method: 'POST',
    data,
  })
}

/** 编辑菜单 */
export function updateMenu(id: number, data: MenuFormParams): Promise<MenuItem> {
  return request<MenuItem>({
    url: `/menu/${id}`,
    method: 'PUT',
    data,
  })
}

/** 删除菜单（存在子菜单时由后端拒绝） */
export function deleteMenu(id: number): Promise<null> {
  return request<null>({
    url: `/menu/${id}`,
    method: 'DELETE',
  })
}

/** 配置角色数据范围（all/dept/self） */
export function updateRoleDataScope(id: number, dataScope: DataScope): Promise<RoleItem> {
  return request<RoleItem>({
    url: `/role/${id}/data-scope`,
    method: 'PUT',
    data: { dataScope },
  })
}
