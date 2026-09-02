/**
 * 用户模块接口
 */
import request from '@/utils/request'

/** 登录入参 */
export interface LoginParams {
  username: string
  password: string
}

/** 登录返回（字段以实际后端为准） */
export interface LoginResult {
  token: string
  user?: UserInfo
}

/** 用户信息 */
export interface UserInfo {
  id: number
  username: string
  nickname: string
  avatar?: string
  email?: string
  /** 手机号（敏感字段：持久化时会被剔除，见 src/store/userStore.ts 的 partialize） */
  phone?: string
  /** 角色标识（如 'admin'），用于权限判定（admin 通配，见 hooks/usePermission.ts） */
  role?: string
  /** 权限标识列表（如 'dashboard:view'），空数组表示无特殊权限 */
  permissions?: string[]
}

/** 登录 */
export function login(data: LoginParams): Promise<LoginResult> {
  return request<LoginResult>({
    url: '/auth/login',
    method: 'POST',
    data,
  })
}

/** 获取当前用户信息（姓名、头像、权限列表等） */
export function getUserInfo(): Promise<UserInfo> {
  return request<UserInfo>({
    url: '/user/info',
    method: 'GET',
  })
}

/**
 * 后端下发的菜单树节点
 * - 菜单结构 / 可见性完全由后端按角色过滤后返回
 * - icon 为图标名称字符串；第二套管理后台外壳（含 MENU_ICON_MAP 映射）已摘除，
 *   该字段保留以兼容后端数据结构，后续如需菜单驱动可自行映射为 lucide 组件
 */
export interface MenuTreeNode {
  path: string
  title: string
  /** 图标名称（如 'system' / 'user'），缺省不显示图标 */
  icon?: string
  /** 权限码（后端已按角色过滤，前端不再参与判定，仅作展示信息保留） */
  authority?: string
  children?: MenuTreeNode[]
}

/** 获取当前用户可见菜单树（按角色由后端过滤后返回） */
export function getMenus(): Promise<MenuTreeNode[]> {
  return request<MenuTreeNode[]>({
    url: '/auth/menus',
    method: 'GET',
  })
}

/** 用户列表查询入参（分页 + 搜索） */
export interface UserListParams {
  /** 当前页码，从 1 开始 */
  page: number
  /** 每页条数 */
  pageSize: number
  /** 用户名关键字（模糊搜索，可选） */
  keyword?: string
}

/** 用户列表项（严格类型，供表格渲染） */
export interface UserListItem {
  id: number
  username: string
  email: string
  /** 角色标识（如 'admin' / 'manager' / 'user'） */
  role: string
  /** 状态：1 启用 / 0 禁用 */
  status: 0 | 1
  /** 创建时间（后端返回格式，直接展示） */
  createdAt: string
}

/** 用户列表分页返回 */
export interface UserListResult {
  list: UserListItem[]
  total: number
}

/** 获取用户列表（分页 + 搜索） */
export function getUserList(params: UserListParams): Promise<UserListResult> {
  return request<UserListResult>({
    url: '/user/list',
    method: 'GET',
    params,
  })
}

/**
 * 获取大数据量导出 Mock 数据（5000 条）
 * 【Why】仅本地性能验证用：.env.development 的 VITE_EXPORT_LARGE_MOCK=true 时，
 * 导出页调用本接口（dev server 中间件返回，生产环境无此路由，返回 404 属预期）。
 */
export function getExportMockUsers(): Promise<UserListResult> {
  return request<UserListResult>({
    url: '/export/users',
    method: 'GET',
  })
}

/** 删除用户 */
export function deleteUser(id: number): Promise<null> {
  return request<null>({
    url: `/user/${id}`,
    method: 'DELETE',
  })
}

/** 新增用户入参（用户名必填且唯一；role 仅 admin/manager/user） */
export interface CreateUserParams {
  username: string
  email?: string
  role?: 'admin' | 'manager' | 'user'
  status?: 0 | 1
}

/** 编辑用户入参（用户名不可改） */
export interface UpdateUserParams {
  email?: string
  role?: 'admin' | 'manager' | 'user'
  status?: 0 | 1
}

/** 新增用户 */
export function createUser(data: CreateUserParams): Promise<UserListItem> {
  return request<UserListItem>({
    url: '/user',
    method: 'POST',
    data,
  })
}

/** 编辑用户 */
export function updateUser(id: number, data: UpdateUserParams): Promise<UserListItem> {
  return request<UserListItem>({
    url: `/user/${id}`,
    method: 'PUT',
    data,
  })
}
