/**
 * routes.tsx 权限判定逻辑单元测试
 * ------------------------------------------------
 * 覆盖对象：
 *   - hasAuthority：权限码集合模型（公开 / admin 通配 / permissions 命中）
 *   - filterMenus：菜单树过滤（hideInMenu 剔除 / 权限剔除 / 空分组剔除）
 * 角色场景：admin（通配）与 user（按 permissions 集合判定）
 */
import { describe, expect, it, vi } from 'vitest'
import { appRoutes, filterMenus, hasAuthority, type AppRouteObject } from './routes'

/** 过滤期间 filterMenus 内部会打调试日志，测试中静默，保持输出干净 */
vi.spyOn(console, 'log').mockImplementation(() => {})

/** 提取菜单树标题（一级 + 二级），便于断言过滤结果 */
function titles(routes: AppRouteObject[]): string[] {
  return routes.flatMap((r) => [
    r.meta?.title,
    ...(r.children ?? []).map((c) => c.meta?.title),
  ].filter((t): t is string => Boolean(t)))
}

describe('hasAuthority 权限判定', () => {
  it('无权限码的路由视为公开：任意角色/权限都放行', () => {
    expect(hasAuthority(undefined, 'user', [])).toBe(true)
    expect(hasAuthority(undefined, 'admin', [])).toBe(true)
    expect(hasAuthority(undefined, undefined, undefined)).toBe(true)
  })

  it('admin 角色通配：即使 permissions 不含该权限码也放行', () => {
    expect(hasAuthority('system:user:view', 'admin', ['dashboard:view'])).toBe(true)
    expect(hasAuthority('system:user:view', 'admin', [])).toBe(true)
    expect(hasAuthority('system:user:view', 'admin')).toBe(true)
  })

  it('user 角色命中 permissions 集合时放行', () => {
    expect(hasAuthority('system:view', 'user', ['dashboard:view', 'system:view'])).toBe(true)
  })

  it('user 角色未命中权限码时拒绝', () => {
    expect(hasAuthority('system:user:view', 'user', ['dashboard:view', 'system:view'])).toBe(false)
  })

  it('user 角色无 permissions（缺省）时拒绝任何带权限码的路由', () => {
    expect(hasAuthority('system:view', 'user')).toBe(false)
    expect(hasAuthority('system:view', 'user', [])).toBe(false)
  })
})

describe('filterMenus 菜单过滤（自定义路由树）', () => {
  const tree: AppRouteObject[] = [
    { path: '/home', meta: { title: '首页' } },
    { path: '/secret', meta: { title: '机密页', authority: 'admin' } },
    { path: '/hidden', meta: { title: '隐藏页', hideInMenu: true } },
    {
      path: '/group',
      meta: { title: '分组', authority: 'system:view' },
      children: [
        { path: '/group/a', meta: { title: '子项A', authority: 'system:a:view' } },
        { path: '/group/b', meta: { title: '子项B', authority: 'system:view' } },
      ],
    },
  ]

  it('user 有 system:view：分组保留，权限不足的子项剔除', () => {
    const result = filterMenus(tree, 'user', ['system:view'])
    expect(titles(result)).toEqual(['首页', '分组', '子项B'])
  })

  it('user 无任何权限码：分组子项全剔除 → 空分组一并隐藏', () => {
    const result = filterMenus(tree, 'user', [])
    expect(titles(result)).toEqual(['首页'])
  })

  it('admin 通配：除 hideInMenu 外全部保留', () => {
    const result = filterMenus(tree, 'admin', [])
    expect(titles(result)).toEqual(['首页', '机密页', '分组', '子项A', '子项B'])
  })
})

describe('filterMenus 真实路由配置（appRoutes）', () => {
  it('user 仅含 system:view：系统管理分组保留，仅系统日志子项可见', () => {
    const result = filterMenus(appRoutes, 'user', ['dashboard:view', 'workbench:use', 'system:view'])
    const titlesArr = titles(result)
    expect(titlesArr).toContain('系统管理')
    expect(titlesArr).toContain('系统日志')
    // 权限不足的子菜单对 user 隐藏
    expect(titlesArr).not.toContain('用户列表')
    expect(titlesArr).not.toContain('角色权限')
    expect(titlesArr).not.toContain('操作审计')
    expect(titlesArr).not.toContain('消息中心')
  })

  it('admin 通配：系统管理全部子菜单可见', () => {
    const result = filterMenus(appRoutes, 'admin', [])
    const titlesArr = titles(result)
    expect(titlesArr).toContain('系统管理')
    expect(titlesArr).toContain('用户列表')
    expect(titlesArr).toContain('系统日志')
  })

  it('user 无任何权限码：系统管理分组整体隐藏', () => {
    const result = filterMenus(appRoutes, 'user', [])
    const titlesArr = titles(result)
    expect(titlesArr).not.toContain('系统管理')
    expect(titlesArr).not.toContain('系统日志')
  })
})
