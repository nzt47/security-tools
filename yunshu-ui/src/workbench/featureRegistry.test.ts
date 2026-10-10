import { describe, expect, it } from 'vitest'
import { DISCOVERED_FEATURES } from './featureRegistry'
import { HUB_NAV, flattenNav } from './hubNav'

describe('featureRegistry：宿主功能自动发现（「新功能零清单进导航」的守卫）', () => {
  it('features/extensions 已并入「插件与扩展」页，不再是自动发现项（禁止双入口）', () => {
    // 阶段 1.2：原 features/extensions 搬到 pages/hub/plugins/extensions.tsx 并删掉 manifest
    expect(DISCOVERED_FEATURES.map((f) => f.key)).not.toContain('extensions')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'extensions')).toBe(false)
    // 合并后的唯一入口：系统组件 → 插件与扩展
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'components/plugins')).toBe(true)
  })

  it('features/route-coverage 也被自动发现（当前唯一实例）', () => {
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
