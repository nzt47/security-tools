/**
 * 快捷键判定（纯函数）—— Ctrl/⌘+, 与 Ctrl/⌘+/ 只认这两个组合。
 */
import { describe, expect, it } from 'vitest'
import { isSettingsShortcut, isShortcutHelpShortcut, SHORTCUT_ITEMS } from './shortcuts'

function key(overrides: Partial<KeyboardEvent> = {}): Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'> {
  return { key: ',', ctrlKey: false, metaKey: false, altKey: false, ...overrides }
}

describe('全局快捷键判定', () => {
  it('Ctrl+, / ⌘+, 命中；Alt 组合不命中', () => {
    expect(isSettingsShortcut(key({ ctrlKey: true }))).toBe(true)
    expect(isSettingsShortcut(key({ metaKey: true }))).toBe(true)
    expect(isSettingsShortcut(key({ ctrlKey: true, altKey: true }))).toBe(false)
    expect(isSettingsShortcut(key({ key: '/', ctrlKey: true }))).toBe(false)
  })

  it('Ctrl+/ / ⌘+/ 命中', () => {
    expect(isShortcutHelpShortcut(key({ key: '/', ctrlKey: true }))).toBe(true)
    expect(isShortcutHelpShortcut(key({ key: '/', metaKey: true }))).toBe(true)
    expect(isShortcutHelpShortcut(key({ key: ',', ctrlKey: true }))).toBe(false)
  })

  it('快捷键表覆盖弹窗自身用到的两条组合', () => {
    const keys = SHORTCUT_ITEMS.map((item) => item.keys.join('+'))
    expect(keys).toContain('Ctrl/⌘+,')
    expect(keys).toContain('Ctrl/⌘+/')
  })
})
