/**
 * userStore 单元测试
 * 覆盖：模拟登录（token / userInfo 写入 + localStorage 持久化）、
 *      token 更新、logout 双源清理、persist 剔除敏感字段（phone）。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useUserStore } from '@/store/userStore'
import { getToken, setToken as saveToken } from '@/utils/request'

/** persist 持久化到 localStorage 的 key（与 userStore.ts 配置保持一致） */
const STORAGE_KEY = 'yunshu-user-store'

/** 读取 persist 写入 localStorage 的原始对象（断言其已存在） */
function readPersisted() {
  const raw = localStorage.getItem(STORAGE_KEY)
  expect(raw).not.toBeNull()
  return JSON.parse(raw!) as { state: { token: string | null; userInfo: Record<string, unknown> | null } }
}

beforeEach(() => {
  // 【Why】隔离用例：清空 localStorage（含 'token' 凭证与 persist 数据）并重置 store 内存态，
  // 防止用例间互相污染
  localStorage.clear()
  useUserStore.setState({ token: null, userInfo: null })
  useUserStore.persist.clearStorage()
})

describe('模拟登录', () => {
  it('写入 token 与 userInfo 后，内存态与 localStorage 均生效（刷新不丢失）', () => {
    // 复刻登录页写入路径（src/pages/Login/index.tsx）：request.ts 凭证 + store 双写
    saveToken('test-token-123')
    useUserStore.getState().setToken('test-token-123')
    useUserStore.getState().setUserInfo({ id: 1, username: 'alice', nickname: '爱丽丝' })

    // 内存态
    expect(useUserStore.getState().token).toBe('test-token-123')
    expect(useUserStore.getState().userInfo?.nickname).toBe('爱丽丝')

    // request.ts 凭证（axios 拦截器 / AdminGuard 读取的来源）
    expect(getToken()).toBe('test-token-123')

    // persist 持久化（刷新页面后可恢复）
    const persisted = readPersisted()
    expect(persisted.state.token).toBe('test-token-123')
    expect(persisted.state.userInfo?.username).toBe('alice')
  })
})

describe('token 更新', () => {
  it('setToken 覆盖旧值并同步持久化', () => {
    useUserStore.getState().setToken('token-1')
    useUserStore.getState().setToken('token-2')

    expect(useUserStore.getState().token).toBe('token-2')
    expect(readPersisted().state.token).toBe('token-2')
  })
})

describe('logout', () => {
  it('清空 store 的 token 与 userInfo，并同步清除 localStorage 凭证', () => {
    saveToken('test-token-123')
    useUserStore.getState().setToken('test-token-123')
    useUserStore.getState().setUserInfo({ id: 1, username: 'alice', nickname: '爱丽丝' })

    useUserStore.getState().logout()

    // store 内存态清空
    expect(useUserStore.getState().token).toBeNull()
    expect(useUserStore.getState().userInfo).toBeNull()
    // request.ts 凭证同步清除（logout 内部调用 clearToken）
    expect(getToken()).toBeNull()
    // persist 持久化同步置空
    expect(readPersisted().state.token).toBeNull()
  })
})

describe('persist 剔除敏感字段', () => {
  it('userInfo 中的 phone 不写入 localStorage，其余字段保留', () => {
    useUserStore.getState().setUserInfo({
      id: 1,
      username: 'alice',
      nickname: '爱丽丝',
      phone: '13800138000',
    })

    // 敏感字段不落盘：直接搜索原始字符串兜底，避免仅断言结构遗漏
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain('13800138000')
    const userInfo = readPersisted().state.userInfo
    expect(userInfo?.phone).toBeUndefined()
    // 非敏感字段保留（供刷新后展示 / 权限控制）
    expect(userInfo?.username).toBe('alice')
  })
})
