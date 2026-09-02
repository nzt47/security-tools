/**
 * usePermission hook 单元测试
 * ------------------------------------------------
 * 覆盖权限判定三分支（与后端 PermissionManager.has_permission 语义一致）：
 *   - 空权限码 → 公开
 *   - admin 角色 → 通配
 *   - 其余角色 → 命中 userInfo.permissions 集合
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useUserStore } from '@/store/userStore'
import type { UserInfo } from '@/api/user'
import { usePermission } from './usePermission'

/** 构造最小 UserInfo（仅测试权限判定所需字段） */
function setUser(overrides: Partial<UserInfo> & { role?: string }) {
  useUserStore.setState({
    userInfo: {
      id: 1,
      username: 'tester',
      nickname: '测试用户',
      ...overrides,
    } as UserInfo,
  })
}

describe('usePermission 权限判定', () => {
  beforeEach(() => {
    useUserStore.setState({ userInfo: null })
  })

  it('空权限码视为公开：任意用户均返回 true', () => {
    setUser({ role: 'user', permissions: [] })
    const { result } = renderHook(() => usePermission(''))
    expect(result.current).toBe(true)
  })

  it('admin 角色通配：即使 permissions 不含该权限码也返回 true', () => {
    setUser({ role: 'admin', permissions: [] })
    const { result } = renderHook(() => usePermission('system:log:export'))
    expect(result.current).toBe(true)
  })

  it('非 admin 命中 permissions 集合时返回 true', () => {
    setUser({ role: 'user', permissions: ['system:log:export'] })
    const { result } = renderHook(() => usePermission('system:log:export'))
    expect(result.current).toBe(true)
  })

  it('非 admin 未命中权限码时返回 false', () => {
    setUser({ role: 'user', permissions: ['system:view'] })
    const { result } = renderHook(() => usePermission('system:log:export'))
    expect(result.current).toBe(false)
  })

  it('未登录（userInfo 为空）且权限码非空时返回 false', () => {
    const { result } = renderHook(() => usePermission('system:log:export'))
    expect(result.current).toBe(false)
  })
})
