import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { getUserInfo, type UserInfo } from '@/api/user'
import { clearToken } from '@/utils/request'

/** 【Why】敏感字段黑名单：持久化时从 userInfo 中剔除，避免手机号等明文落盘 localStorage */
const SENSITIVE_FIELDS: readonly string[] = ['phone']

/** 全局用户状态接口 */
interface UserState {
  /** 登录令牌，null 表示未登录 */
  token: string | null
  /** 当前登录用户信息，null 表示未加载或未登录 */
  userInfo: UserInfo | null
  /** 设置登录令牌（同时应配合设置 userInfo） */
  setToken: (token: string) => void
  /** 设置用户信息 */
  setUserInfo: (userInfo: UserInfo) => void
  /** 拉取当前用户信息并写入 Store；失败时抛出异常，由调用方决定跳转策略 */
  fetchUserInfo: () => Promise<UserInfo>
  /** 退出登录：清空 Store 状态，并清除 localStorage 中的凭证 */
  logout: () => void
}

export const useUserStore = create<UserState>()(
  persist(
    (set) => ({
      token: null,
      userInfo: null,
      // 【Why】setToken 只负责 token；userInfo 用 setUserInfo 独立设置，避免耦合
      setToken: (token) => set({ token }),
      setUserInfo: (userInfo) => set({ userInfo }),
      // 【Why】拉取当前用户信息并同步到 Store；错误向上抛出，让调用方（如 MainLayout）处理登出跳转
      fetchUserInfo: async () => {
        const userInfo = await getUserInfo()
        set({ userInfo })
        return userInfo
      },
      // 【Why】axios 拦截器与路由守卫读取 localStorage 'token'，此处复用 request.ts 的 clearToken 一并清除，
      // 保证登出后凭证彻底失效（避免重复引用 token key，出现 TOKEN_KEY 未定义问题）
      logout: () => {
        clearToken()
        set({ token: null, userInfo: null })
      },
    }),
    {
      // 【Why】默认存到 localStorage，刷新页面后状态不丢失
      name: 'yunshu-user-store',
      // 【Why】只持久化非敏感状态：token + 剔除敏感字段后的 userInfo；
      // 刷新后 phone 等字段为空，需要展示时由 fetchUserInfo 重新拉取
      partialize: (state) => ({
        token: state.token,
        userInfo: state.userInfo
          ? (Object.fromEntries(
              Object.entries(state.userInfo).filter(([key]) => !SENSITIVE_FIELDS.includes(key)),
            ) as UserInfo)
          : null,
      }),
    }
  )
)
