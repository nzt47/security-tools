/**
 * 插件客户端 UI 宿主（T4.2 落地）—— 「插件自带 UI 自动挂上」的机制。
 *
 * 约定（与 public/plugins/demo-ui.js 同源）：插件把自己的原生 ES 模块放在
 * yunshu-ui/public/plugins/<name>.js，并在后端 Plugin.client_slot 里声明
 * { slotId, module }；宿主用动态 import(module) 加载后调用模块导出的
 * register(registry)，模块用 registry.createElement 造组件、用
 * registry.mountToSlot(slotId, entry) 声明「我要挂一个界面」。
 *
 * 【为什么用 public/ 下的原生 ES 模块】它不经 Vite 转译，插件作者无需进主仓构建；
 * 代价是不能 import React/JSX，故 registry 把 createElement 暴露给模块。
 *
 * 【失败必须响】加载失败 / 没有导出 register ⇒ 返回 state='error' + error 文案，
 * 由调用方上屏；绝不静默当一个空列表（本仓「静默失效」家族已记录多次）。
 */
import * as React from 'react'

export interface SlotEntry {
  id: string
  title?: string
  icon?: string
  order?: number
  component?: React.ComponentType<Record<string, unknown>> | (() => React.ReactNode)
  plugin?: string
}

export interface SlotRegistryFacade {
  createElement: typeof React.createElement
  mountToSlot: (slotId: string, entry: SlotEntry) => void
  extendProfile: (slotId: string, patch: Record<string, unknown>) => void
  openPanel: (id: string) => void
}

export interface MountResult {
  plugin: string
  module: string
  state: 'ok' | 'error'
  error?: string
  entries: SlotEntry[]
}

export type ModuleLoader = (url: string) => Promise<unknown>

const defaultLoader: ModuleLoader = (url) => import(/* @vite-ignore */ url)

/**
 * 把插件声明的客户端模块 URL 解析成"当前运行形态下真的能加载"的 URL。
 *
 * 【为什么需要】(2026-10-16 实测) 插件按约定把原生 ES 模块放
 * `yunshu-ui/public/plugins/<name>.js`，并声明 `module: "/plugins/<name>.js"` ——
 * 这在 Vite 开发态成立（public/ 挂根路径）。但 Flask 生产态由 `build:flask` 把产物
 * 复制到 `static/plugins/`、经 `/static/*` 托管；`/plugins/*` 既没有路由、又不在
 * `CP_API_AUTH_ALLOW` 里，会被全局鉴权闸门（enforce_all）拦成 **401**，动态 import
 * 直接报 "Failed to fetch dynamically imported module"。
 *
 * 故生产构建（`import.meta.env.DEV === false`）把 `/plugins/` 归一到
 * `/static/plugins/`；开发态保持原样。旧的 `importClientModule` 回退逻辑随 legacy
 * 插件宿主在 9b4aa8f5 被删除，这里按同一意图在新宿主上重建。
 */
export function resolvePluginModuleUrl(url: string, isDev: boolean = import.meta.env.DEV): string {
  if (!isDev && url.startsWith('/plugins/')) return '/static' + url
  return url
}

/** 造一个 per-plugin 的门面：mountToSlot 把条目收进 entries（不认识 slotId 也照收）。 */
export function createSlotFacade(
  plugin: string,
  entries: SlotEntry[],
  onOpen?: (id: string) => void,
): SlotRegistryFacade {
  return {
    createElement: React.createElement,
    mountToSlot: (_slotId, entry) => { entries.push({ ...entry, plugin }) },
    // profile.json 插槽宿主体系未迁移（见 plugin-manage.tsx 头注）；T4.2 只消费 mountToSlot。
    extendProfile: () => { /* 有意为空：未迁移 */ },
    openPanel: (id) => onOpen?.(id),
  }
}

export async function registerPluginModule(
  plugin: string,
  moduleUrl: string,
  opts: { onOpen?: (id: string) => void; load?: ModuleLoader; isDev?: boolean } = {},
): Promise<MountResult> {
  const entries: SlotEntry[] = []
  const resolvedUrl = resolvePluginModuleUrl(moduleUrl, opts.isDev)
  try {
    const mod = (await (opts.load ?? defaultLoader)(resolvedUrl)) as {
      register?: (r: SlotRegistryFacade) => void
      default?: (r: SlotRegistryFacade) => void
    }
    const register = mod?.register ?? mod?.default
    if (typeof register !== 'function') {
      throw new Error('模块未导出 register(registry) —— 约定见 public/plugins/demo-ui.js')
    }
    register(createSlotFacade(plugin, entries, opts.onOpen))
    return { plugin, module: resolvedUrl, state: 'ok', entries }
  } catch (e) {
    return {
      plugin,
      module: resolvedUrl,
      state: 'error',
      entries: [],
      error: e instanceof Error ? e.message : String(e),
    }
  }
}
