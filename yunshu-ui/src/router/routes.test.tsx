/**
 * 权限路由函数测试（对应 M1 测试计划 T10）
 * 覆盖：hasAuthority（公开路由/admin 通配/权限码命中与未命中）、filterMenus（hideInMenu、权限过滤、空分组隐藏）。
 */
import { describe, expect, it, vi } from 'vitest'
import { filterMenus, hasAuthority, type AppRouteObject } from './routes'

describe('权限判定', () => {
  it('hasAuthority：无权限码公开、admin 通配、普通角色按权限码命中', () => {
    // 无权限码 → 登录用户均可访问
    expect(hasAuthority(undefined, 'user', [])).toBe(true)
    // admin 角色通配（即使权限列表为空）
    expect(hasAuthority('system:view', 'admin', [])).toBe(true)
    // 普通角色：权限码命中
    expect(hasAuthority('system:view', 'user', ['system:view'])).toBe(true)
    // 普通角色：权限码未命中
    expect(hasAuthority('system:view', 'user', ['dashboard:view'])).toBe(false)
  })

  it('filterMenus：剔除 hideInMenu、无权限节点，空分组整体隐藏', () => {
    vi.spyOn(console, 'log').mockImplementation(() => {})

    const routes: AppRouteObject[] = [
      { path: '/a', meta: { title: 'A' } },
      { path: '/b', meta: { title: 'B', hideInMenu: true } },
      {
        path: '/group',
        meta: { title: 'G', authority: 'g:view' },
        children: [
          { path: '/group/x', meta: { title: 'X', authority: 'g:view' } },
          { path: '/group/y', meta: { title: 'Y', authority: 'g:other' } },
        ],
      },
      { path: '/empty', meta: { title: 'E', authority: 'e:view' }, children: [] },
    ]

    const filtered = filterMenus(routes, 'user', ['g:view'])
    // 保留公开路由与有权限的分组，剔除 hideInMenu 与无权限节点
    expect(filtered.map((r) => r.path)).toEqual(['/a', '/group'])
    // 分组内仅保留有权限的子项
    expect(filtered[1]?.children?.map((c) => c.path)).toEqual(['/group/x'])
  })
})
