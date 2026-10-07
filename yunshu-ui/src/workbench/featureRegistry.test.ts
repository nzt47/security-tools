import { describe, expect, it } from 'vitest'
import { DISCOVERED_FEATURES } from './featureRegistry'
import { HUB_NAV, flattenNav } from './hubNav'

describe('featureRegistry：宿主功能自动发现（「新功能零清单进导航」的守卫）', () => {
  it('features/extensions 被自动发现并挂进 HUB_NAV', () => {
    expect(DISCOVERED_FEATURES.map((f) => f.key)).toContain('extensions')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'extensions')).toBe(true)
  })

  it('features/route-coverage 也被自动发现（本批新增的实例）', () => {
    expect(DISCOVERED_FEATURES.map((f) => f.key)).toContain('route-coverage')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'route-coverage')).toBe(true)
  })

  it('每个被发现的功能都四件齐全（缺一不上导航）', () => {
    for (const f of DISCOVERED_FEATURES) {
      expect(f.key, 'key').toBeTruthy()
      expect(f.label, 'label').toBeTruthy()
      expect(f.icon, 'icon').toBeTruthy()
      expect(f.component, 'component').toBeTruthy()
    }
  })

  it('非空转：至少发现 1 个（否则 glob 坏了也会全绿）', () => {
    expect(DISCOVERED_FEATURES.length).toBeGreaterThan(0)
  })
})
