/**
 * 宿主功能自动发现（feature registry）
 * ================================================
 * 这是「以后开发新功能必须能进 UI」的机制之一（另一条是插件面：/api/plugins 的
 * schema + client_slot，见 plugins/extensionHost.ts 与 features/extensions）。
 *
 * 约定（新增一个**宿主功能页**只要做这一件事）：
 *   在 yunshu-ui/src/features/<key>/index.tsx 里
 *     · 导出 default —— 无 props 的页面组件；
 *     · 导出 manifest —— { key, label, icon, order? }。
 *   构建时由 import.meta.glob(eager) 静态发现，**无需改 hubNav.tsx、无需改任何清单**。
 *
 * 【为什么用 eager glob】HUB_NAV 是模块级同步常量，NavPanel/ContentPanel 直接消费；
 *   eager glob 在模块加载期即同步返回，故新功能「零清单」上导航。
 *
 * 【边界（如实标注）】
 *   1. 只发现 features/<key>/index.tsx 这一层，且必须同时有 default + manifest；
 *      只有 manifest 没有 default 的目录**不会**上导航（避免点进去空白）；
 *   2. 组件若需要导航参数 props，走既有的 derivePanelParams 约定（key 即参数）；
 *   3. 不替代插件面：插件自带的 UI 由 /api/plugins 的 client_slot 驱动。
 */
import type { HubNavItem } from './hubNav'

export interface FeatureManifest {
  key: string
  label: string
  icon: HubNavItem['icon']
  /** 排序权重（升序；不填按 1000，排在静态宿主导航之后） */
  order?: number
}

interface FeatureModule {
  default?: HubNavItem['component']
  manifest?: FeatureManifest
}

const modules = import.meta.glob<FeatureModule>('../features/*/index.tsx', { eager: true })

export const DISCOVERED_FEATURES: HubNavItem[] = Object.entries(modules)
  .filter(([, m]) => Boolean(m && m.manifest && m.default))
  .sort((a, b) => (a[1].manifest?.order ?? 1000) - (b[1].manifest?.order ?? 1000))
  .map(([, m]) => ({
    key: m.manifest!.key,
    label: m.manifest!.label,
    icon: m.manifest!.icon,
    component: m.default!,
  }))
