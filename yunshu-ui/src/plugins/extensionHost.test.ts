import { describe, expect, it } from 'vitest'
import { createSlotFacade, registerPluginModule } from './extensionHost'

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
})
