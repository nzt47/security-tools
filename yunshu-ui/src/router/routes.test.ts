/**
 * routes.tsx 权限判定逻辑单元测试
 * ------------------------------------------------
 * 覆盖对象：
 *   - hasAuthority：权限码集合模型（公开 / admin 通配 / permissions 命中）
 *   - filterMenus：菜单树过滤（hideInMenu 剔除 / 权限剔除 / 空分组剔除）
 * 角色场景：admin（通配）与 user（按 permissions 集合判定）
 *
 * 注：原「真实路由配置（appRoutes）」用例随第二套管理后台外壳（appRoutes 配置树）
 * 一并移除——页面组件已收敛到统一工作台 hubNav「admin」分组，见 src/router/index.tsx。
 */
import { describe, expect, it, vi } from 'vitest'
import { filterMenus, hasAuthority, type AppRouteObject } from './routes'

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
