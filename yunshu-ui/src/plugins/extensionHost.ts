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
  opts: { onOpen?: (id: string) => void; load?: ModuleLoader } = {},
): Promise<MountResult> {
  const entries: SlotEntry[] = []
  try {
    const mod = (await (opts.load ?? defaultLoader)(moduleUrl)) as {
      register?: (r: SlotRegistryFacade) => void
      default?: (r: SlotRegistryFacade) => void
    }
    const register = mod?.register ?? mod?.default
    if (typeof register !== 'function') {
      throw new Error('模块未导出 register(registry) —— 约定见 public/plugins/demo-ui.js')
    }
    register(createSlotFacade(plugin, entries, opts.onOpen))
    return { plugin, module: moduleUrl, state: 'ok', entries }
  } catch (e) {
    return {
      plugin,
      module: moduleUrl,
      state: 'error',
      entries: [],
      error: e instanceof Error ? e.message : String(e),
    }
  }
}
