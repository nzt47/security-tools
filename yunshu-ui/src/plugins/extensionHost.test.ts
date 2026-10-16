import { describe, expect, it } from 'vitest'
import { createSlotFacade, registerPluginModule, resolvePluginModuleUrl } from './extensionHost'

describe('extensionHost：插件自带的原生 ES 模块宿主', () => {
  it('register(registry) 里 mountToSlot 的条目被收集（ok）', async () => {
    const mod = {
      register: (r: { mountToSlot: (s: string, e: { id: string }) => void }) =>
        r.mountToSlot('panels', { id: 'demo-ui' }),
    }
    const res = await registerPluginModule('demo', '/plugins/demo-ui.js', { load: async () => mod })
    expect(res.state).toBe('ok')
    expect(res.entries.map((e) => e.id)).toEqual(['demo-ui'])
    expect(res.entries[0].plugin).toBe('demo')
  })

  it('模块没有 register 导出 ⇒ 响亮失败（不是静默空列表）', async () => {
    const res = await registerPluginModule('demo', '/x.js', { load: async () => ({}) })
    expect(res.state).toBe('error')
    expect(res.error).toContain('register')
  })

  it('加载抛错 ⇒ 记录 error 文案', async () => {
    const res = await registerPluginModule('demo', '/x.js', {
      load: async () => { throw new Error('boom') },
    })
    expect(res.state).toBe('error')
    expect(res.error).toContain('boom')
  })

  it('门面暴露 React.createElement 与 openPanel 回调', async () => {
    const seen: string[] = []
    const f = createSlotFacade('demo', [], (id) => seen.push(id))
    expect(typeof f.createElement).toBe('function')
    expect(f.createElement('div', null, 'hi')).toBeTruthy()
    f.openPanel('demo-ui')
    expect(seen).toEqual(['demo-ui'])
  })

  it('生产态（isDev=false）经 /static/plugins/ 取址，并回填解析后的 module', async () => {
    const seen: string[] = []
    const mod = { register: () => {} }
    const res = await registerPluginModule('demo', '/plugins/demo-ui.js', {
      isDev: false,
      load: async (url: string) => { seen.push(url); return mod },
    })
    expect(seen).toEqual(['/static/plugins/demo-ui.js'])
    expect(res.module).toBe('/static/plugins/demo-ui.js')
    expect(res.state).toBe('ok')
  })
})

describe('resolvePluginModuleUrl：开发/生产两态取址', () => {
  it('开发态保持 /plugins/ 原样（Vite public/ 挂根路径）', () => {
    expect(resolvePluginModuleUrl('/plugins/demo-ui.js', true)).toBe('/plugins/demo-ui.js')
  })

  it('生产态把 /plugins/ 归一到 /static/plugins/（Flask 托管）', () => {
    expect(resolvePluginModuleUrl('/plugins/demo-ui.js', false)).toBe('/static/plugins/demo-ui.js')
  })

  it('已是 /static/ 或外部 URL 不改编址', () => {
    expect(resolvePluginModuleUrl('/static/plugins/demo-ui.js', false)).toBe('/static/plugins/demo-ui.js')
    expect(resolvePluginModuleUrl('https://cdn.example.com/x.js', false)).toBe('https://cdn.example.com/x.js')
    expect(resolvePluginModuleUrl('/plugins/x.js', true)).toBe('/plugins/x.js')
  })
})

