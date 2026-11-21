/**
 * IA 重组（2026-11-21）的深链落点契约测试
 * ------------------------------------------------------------------
 * 这一层专门守"旧叶子 key → 合并页的哪个 Tab"。浏览器级复核在本机不稳（一次连跑几十个页面会把
 * Windows Job runner 拖崩），所以把"落点"变成纯查表断言：容器各自导出 LEGACY 映射，
 * 这里对每一条旧 key 同时校验两件事 ——
 *   ① hubNav 的 LEGACY_NAV_KEY_MAP 把它解析到哪个**叶子**；
 *   ② 那个叶子的容器把它映射到哪个**Tab**。
 * 两者都对，才等于"点旧收藏能直接看到原来那一屏"。
 */
import { describe, expect, it } from 'vitest'
import { LEGACY_NAV_KEY_MAP, resolveNavKey } from '@/workbench/hubNav'
import { LEGACY as RUNTIME_LEGACY } from '@/pages/hub/panorama/runtime'
import { LEGACY as LOGS_LEGACY } from '@/pages/hub/observability/logs'
import { LEGACY as BOARD_LEGACY } from '@/pages/hub/governance/board'
import { LEGACY as ACCOUNTS_LEGACY } from '@/pages/hub/admin/accounts'
import { engineInitialTab } from '@/pages/hub/engine/hub'
import { initialHealthTab } from '@/pages/hub/panorama/health'

/** 旧叶子 key → [落点叶子, 期望 Tab] */
const CASES: Array<[string, string, string]> = [
  // 运行监控
  ['panorama/sensors', 'panorama/runtime', 'sensors'],
  ['panorama/monitor', 'panorama/runtime', 'topology'],
  // 日志与审计
  ['panorama/logs', 'observability/logs', 'live'],
  ['admin/audit', 'observability/logs', 'ops-audit'],
  ['admin/logs', 'observability/logs', 'syslog'],
  ['governance/audit', 'observability/logs', 'chain'],
  ['admin/export', 'observability/logs', 'export'],
  // 治理看板
  ['governance/pipeline', 'governance/board', 'pipeline'],
  ['governance/capabilities', 'governance/board', 'capabilities'],
  ['governance/roi', 'governance/board', 'roi'],
  ['governance/incidents', 'governance/board', 'incidents'],
  ['governance/route-coverage', 'governance/board', 'route-coverage'],
  // 账号与权限
  ['admin/users', 'admin/accounts', 'users'],
  ['admin/roles', 'admin/accounts', 'roles'],
  ['admin/menus', 'admin/accounts', 'menus'],
]

describe('IA 深链落点：旧叶子 key → 合并页的 Tab', () => {
  it('每一条旧 key 都能解析到一个存在的叶子', () => {
    for (const [oldKey, leaf] of CASES) {
      expect(resolveNavKey(oldKey), oldKey).toBe(leaf)
      expect(LEGACY_NAV_KEY_MAP[oldKey], oldKey).toBe(leaf)
    }
  })

  it('容器 LEGACY 表把旧 key 映射到期望的 Tab（逐条）', () => {
    for (const [oldKey, leaf, tab] of CASES) {
      const table: Record<string, string> =
        leaf === 'panorama/runtime' ? RUNTIME_LEGACY
          : leaf === 'observability/logs' ? LOGS_LEGACY
            : leaf === 'governance/board' ? BOARD_LEGACY
              : ACCOUNTS_LEGACY
      expect(table[oldKey], oldKey + ' → ' + leaf).toBe(tab)
    }
  })

  it('容器 LEGACY 表的 key 必须都登记在 hubNav 的兼容层里（否则解析不到叶子，Tab 永远打不开）', () => {
    const all = { ...RUNTIME_LEGACY, ...LOGS_LEGACY, ...BOARD_LEGACY, ...ACCOUNTS_LEGACY }
    for (const key of Object.keys(all)) {
      expect(LEGACY_NAV_KEY_MAP[key], key).toBeDefined()
    }
  })

  it('开关中心并入引擎页：?panel=governance/settings 落 engine 且初始 Tab = settings', () => {
    expect(resolveNavKey('governance/settings')).toBe('engine')
    expect(engineInitialTab('governance/settings')).toBe('settings')
    // 引擎原有两条旧深链不受影响
    expect(engineInitialTab('engine/scheduler')).toBe('scheduler')
    expect(engineInitialTab('')).toBe('heartbeat')
  })

  it('系统管理·仪表盘并入系统总览：?panel=admin/dashboard 落 health 且初始 Tab = admin', () => {
    expect(resolveNavKey('admin/dashboard')).toBe('panorama/health')
    expect(initialHealthTab('admin/dashboard')).toBe('admin')
    expect(initialHealthTab('')).toBe('overview')
  })
})
