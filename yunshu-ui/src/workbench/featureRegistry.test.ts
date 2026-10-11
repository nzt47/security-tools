/**
 * featureRegistry 守卫 —— 「新功能零清单进导航」机制
 * ------------------------------------------------------------------
 * 阶段 1.2 / 阶段 2 之后，真实 src/features/ 目录已无实例（extensions 并入「插件与扩展」、
 * route-coverage 归入治理面板）。因此本文件分两层：
 *   1. 真实层：两个原实例都**不再**自动发现、也不再出现在 HUB_NAV（禁止双入口）；
 *   2. 机制层：buildDiscoveredFeatures 是纯函数，用**合成模块**锁死四件齐全 / 缺件不上导航 /
 *      order 排序 —— 与真实目录有没有实例无关，glob 坏了也不会让机制无人看守。
 */
import { describe, expect, it } from 'vitest'
import { Puzzle } from 'lucide-react'
import { buildDiscoveredFeatures, DISCOVERED_FEATURES, type FeatureModule } from './featureRegistry'
import { HUB_NAV, flattenNav } from './hubNav'

const Comp = () => null

describe('featureRegistry：真实 features 目录（两个原实例均已归位）', () => {
  it('extensions 并入「插件与扩展」页，不再自动发现/不再单列导航', () => {
    expect(DISCOVERED_FEATURES.map((f) => f.key)).not.toContain('extensions')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'extensions')).toBe(false)
    // 合并后的唯一入口：系统组件 → 插件与扩展
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'components/plugins')).toBe(true)
  })

  it('route-coverage 归入治理面板，不再自动发现/不再单列导航', () => {
    expect(DISCOVERED_FEATURES.map((f) => f.key)).not.toContain('route-coverage')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'route-coverage')).toBe(false)
    // 归位后的唯一入口：治理面板 → 路由覆盖
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'governance/route-coverage')).toBe(true)
  })

  it('真实目录当前为空也不崩（机制保留给未来功能）', () => {
    expect(Array.isArray(DISCOVERED_FEATURES)).toBe(true)
  })
})

describe('featureRegistry：机制本身（合成模块 · 与真实目录解耦）', () => {
  it('default + manifest 四件齐全 → 生成导航项', () => {
    const mods: Record<string, FeatureModule> = {
      '../features/demo/index.tsx': {
        default: Comp,
        manifest: { key: 'demo', label: '演示', icon: Puzzle, order: 900 },
      },
    }
    const nav = buildDiscoveredFeatures(mods)
    expect(nav).toHaveLength(1)
    expect(nav[0].key).toBe('demo')
    expect(nav[0].label).toBe('演示')
    expect(nav[0].icon).toBe(Puzzle)
    expect(nav[0].component).toBe(Comp)
  })

  it('缺 default / 缺 manifest 的目录不上导航（避免点进去空白）', () => {
    const mods: Record<string, FeatureModule> = {
      onlyManifest: { manifest: { key: 'only-manifest', label: 'A', icon: Puzzle } },
      onlyDefault: { default: Comp },
      complete: { default: Comp, manifest: { key: 'complete', label: 'C', icon: Puzzle } },
    }
    expect(buildDiscoveredFeatures(mods).map((f) => f.key)).toEqual(['complete'])
  })

  it('按 order 升序；不填 order 按 1000 排在已排序项之后', () => {
    const mk = (key: string, order?: number): FeatureModule => ({
      default: Comp,
      manifest: { key, label: key, icon: Puzzle, order },
    })
    const mods: Record<string, FeatureModule> = {
      x: mk('x'),
      a: mk('a', 5),
      b: mk('b', 1),
    }
    expect(buildDiscoveredFeatures(mods).map((f) => f.key)).toEqual(['b', 'a', 'x'])
  })

  it('每个被发现的功能都四件齐全（对合成结果的形式化复核）', () => {
    const mods: Record<string, FeatureModule> = {
      demo: { default: Comp, manifest: { key: 'demo', label: '演示', icon: Puzzle } },
    }
    for (const f of buildDiscoveredFeatures(mods)) {
      expect(f.key, 'key').toBeTruthy()
      expect(f.label, 'label').toBeTruthy()
      expect(f.icon, 'icon').toBeTruthy()
      expect(f.component, 'component').toBeTruthy()
    }
  })
})
