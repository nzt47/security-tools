/**
 * 登录 → 刷新 → 401 全流程集成测试
 * ------------------------------------------------------
 * 1. 模拟登录：双写 localStorage 凭证与 store（复刻 LoginPage 写入路径）
 * 2. 模拟刷新页面：清空内存态后通过 persist.rehydrate() 从 localStorage 恢复
 *    （即页面加载时 zustand 的恢复路径；rehydrate 会写回，故先备份快照再还原）
 * 3. 触发 401：通过 request config 注入 adapter 返回 401，验证拦截器同步 logout
 *    （store + localStorage 清空、hash 跳转登录页、日志埋点记录 token 脱敏值与调用堆栈）
 *
 * 注：不使用 vi.resetModules / vi.mock 模拟刷新——会使 request 模块进入独立模块图，
 * 其绑定的 userStore 与测试断言的实例不一致（logout 不生效）。
 * 单实例 + persist.rehydrate() 模拟刷新语义等价且稳定（userStore 与 request 已无循环依赖）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUserStore } from '@/store/userStore'
import { getToken, request, setToken as saveToken } from '@/utils/request'

/** persist 持久化到 localStorage 的 key（与 userStore.ts 配置保持一致） */
const STORAGE_KEY = 'yunshu-user-store'

beforeEach(() => {
  // 【Why】隔离用例：清空 localStorage（含 'token' 凭证与 persist 数据）并重置 store 内存态，
  // 重置 hash 避免登录页跳转残留
  localStorage.clear()
  useUserStore.setState({ token: null, userInfo: null })
  useUserStore.persist.clearStorage()
  window.location.hash = ''
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('登录 → 刷新 → 401 全流程', () => {
  it('登录持久化、刷新后恢复、401 触发 logout 同步清理并输出埋点日志', async () => {
    // ---------- 1. 模拟登录（复刻 LoginPage：saveToken + store 双写） ----------
    saveToken('it-token-abcdef123456')
    useUserStore.getState().setToken('it-token-abcdef123456')
    useUserStore.getState().setUserInfo({
      id: 1,
      username: 'alice',
      nickname: '爱丽丝',
      phone: '13800138000',
    })
    expect(useUserStore.getState().token).toBe('it-token-abcdef123456')

    // ---------- 2. 模拟刷新页面：内存态清空后从 localStorage 恢复 ----------
    const snapshot = localStorage.getItem(STORAGE_KEY)!
    // 模拟刷新后内存为空；persist 会拦截 setState 写回 null，故先备份再还原快照
    useUserStore.setState({ token: null, userInfo: null })
    localStorage.setItem(STORAGE_KEY, snapshot)
    await useUserStore.persist.rehydrate()

    expect(useUserStore.getState().token).toBe('it-token-abcdef123456')
    expect(useUserStore.getState().userInfo?.username).toBe('alice')
    // 敏感字段不随持久化恢复（partialize 已剔除 phone）
    expect(useUserStore.getState().userInfo?.phone).toBeUndefined()

    // ---------- 3. 触发 401：通过 request config 注入 adapter 返回 401 响应 ----------
    const infoSpy = vi.spyOn(console, 'info')
    await request({
      url: '/api/protected',
      // 【Why】自定义 adapter 不经过 axios 的 settle 状态码校验，直接返回 401 响应会走成功
      // 拦截器（code!==200 分支）而非 401 error 分支；必须抛带 response 的 error 才能
      // 触发 axios 的 error 分支（拦截器依据 error.response.status 识别 401）。
      // err.config 必须带上：error 分支的 logPerf 会读取 config.method，缺失会抛 TypeError。
      adapter: async (config) => {
        const err = new Error('Request failed with status code 401') as Error & {
          response?: unknown
          config?: unknown
        }
        err.config = config
        err.response = {
          data: { code: 401, message: '登录已过期', data: null },
          status: 401,
          statusText: 'Unauthorized',
          headers: {},
          config,
        }
        throw err
      },
    }).catch(() => undefined)

    // store 与 localStorage 同步清空（401 拦截器调用 logout）
    expect(useUserStore.getState().token).toBeNull()
    expect(useUserStore.getState().userInfo).toBeNull()
    expect(getToken()).toBeNull()

    // HashRouter 下跳转登录页（hash 变为 #/login）
    expect(window.location.hash).toContain('#/login')

    // 日志埋点：记录 token（脱敏：前 8 字符 + … + 后 4 字符）与调用堆栈
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining('[auth] 401 触发 logout'))
    expect(infoSpy).toHaveBeenCalledWith(expect.stringContaining('token=it-token…3456'))
  })
})
