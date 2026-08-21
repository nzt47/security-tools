/**
 * storage 封装单元测试
 * - getJSON/setJSON：JSON 往返、损坏回退、缺失回退
 * - getRaw/setRaw：原样字符串（token 契约键不被 JSON 序列化破坏）
 * - remove / has
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { storage, STORAGE_KEYS } from './storage'

describe('storage 封装', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('setJSON/getJSON 往返正确（结构化数据）', () => {
    const value = { list: [1, 2, 3], name: '云枢' }
    storage.setJSON(STORAGE_KEYS.EDITOR_CODE, value)
    expect(storage.getJSON(STORAGE_KEYS.EDITOR_CODE, { list: [], name: '' })).toEqual(value)
  })

  it('键不存在时 getJSON 返回 fallback', () => {
    expect(storage.getJSON('yunshu:not-exist', 42)).toBe(42)
  })

  it('损坏 JSON 回退 fallback 且不抛异常', () => {
    localStorage.setItem(STORAGE_KEYS.EDITOR_CODE, '{broken json')
    expect(storage.getJSON(STORAGE_KEYS.EDITOR_CODE, 'default')).toBe('default')
  })

  it('setRaw/getRaw 原样字符串（token 契约：不加引号、不转义）', () => {
    storage.setRaw(STORAGE_KEYS.TOKEN, 'abc.def-123')
    // 契约键必须保持原样，路由守卫/拦截器直接读裸键
    expect(localStorage.getItem('token')).toBe('abc.def-123')
    expect(storage.getRaw(STORAGE_KEYS.TOKEN)).toBe('abc.def-123')
  })

  it('remove 删除后 has 为 false', () => {
    storage.setRaw(STORAGE_KEYS.TOKEN, 'x')
    expect(storage.has(STORAGE_KEYS.TOKEN)).toBe(true)
    storage.remove(STORAGE_KEYS.TOKEN)
    expect(storage.has(STORAGE_KEYS.TOKEN)).toBe(false)
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('键名注册表覆盖既有契约键，不引入额外前缀', () => {
    // 既有契约键：token / yunshu-theme，注册表内键名即完整存储键
    expect(STORAGE_KEYS.TOKEN).toBe('token')
    expect(STORAGE_KEYS.THEME).toBe('yunshu-theme')
  })
})
