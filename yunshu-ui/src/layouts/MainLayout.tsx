/**
 * MainLayout —— 主布局：左侧 Sidebar + 顶部 Header + 中间内容区（Outlet）
 * 登录后初始化：挂载时若无 userInfo 则拉取当前用户信息；
 * 加载期间展示全屏骨架屏（防闪烁），拉取失败（如 401）则登出并跳回登录页。
 */
import { useEffect, useRef, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { useUserStore } from '@/store/userStore'
import { logger } from '@/utils/logger'
import Sidebar from '@/components/Sidebar'
import BreadCrumb from '@/components/BreadCrumb'

/** 全屏骨架屏：模拟 Sidebar + Header + 内容区骨架，加载用户信息期间展示，防止页面闪烁 */
function FullScreenSkeleton() {
  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex h-14 items-center border-b border-slate-200 px-4">
          <div className="h-4 w-24 animate-pulse rounded bg-slate-200" />
        </div>
        <div className="space-y-2 p-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-9 animate-pulse rounded-md bg-slate-200" />
          ))}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
          <div className="h-4 w-32 animate-pulse rounded bg-slate-200" />
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 animate-pulse rounded-full bg-slate-200" />
            <div className="h-4 w-16 animate-pulse rounded bg-slate-200" />
          </div>
        </header>
        <main className="flex-1 p-4">
          <div className="h-full animate-pulse rounded-xl bg-slate-200/60" />
        </main>
      </div>
    </div>
  )
}

export default function MainLayout() {
  const navigate = useNavigate()
  const userInfo = useUserStore((s) => s.userInfo)
  const logout = useUserStore((s) => s.logout)
  // 初始：登录页已写入 userInfo 或持久化恢复时不需加载；否则进入骨架屏加载态
  const [initializing, setInitializing] = useState(() => !userInfo)
  // 【Why】React StrictMode 下 effect 会执行两次，用 ref 保证登录后初始化只触发一次
  const initStarted = useRef(false)

  useEffect(() => {
    if (initStarted.current) return
    initStarted.current = true

    async function init() {
      // 已存在用户信息（登录页 setUserInfo / 持久化恢复）则跳过拉取
      if (useUserStore.getState().userInfo) {
        logger.info('[MainLayout] 已存在 userInfo，跳过登录后初始化拉取')
        return
      }
      logger.info('[MainLayout] 未检测到 userInfo，开始拉取用户信息（骨架屏加载中）')
      setInitializing(true)
      try {
        await useUserStore.getState().fetchUserInfo()
        logger.info('[MainLayout] 用户信息拉取成功，退出骨架屏')
      } catch (err) {
        // 拉取失败（典型 401）：会话失效，登出并回登录页
        logger.warn('[MainLayout] 用户信息拉取失败，执行登出并跳转登录页', err)
        logout()
        navigate('/login', { replace: true })
      } finally {
        setInitializing(false)
      }
    }
    void init()
  }, [navigate, logout])

  /** 退出登录：统一走 Store 的 logout（清 Store 状态 + 清 localStorage 凭证），不直接操作 DOM */
  function handleLogout() {
    logger.info('[MainLayout] 用户点击退出登录')
    logout()
    navigate('/login', { replace: true })
  }

  if (initializing) {
    return <FullScreenSkeleton />
  }

  const displayName = userInfo?.nickname || userInfo?.username || '未登录用户'

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      {/* 左侧边栏：菜单数据驱动（见 src/components/Sidebar.tsx） */}
      <Sidebar />

      {/* 右侧：Header + 内容区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
          {/* 面包屑：按当前路径从路由配置读取层级标题 */}
          <BreadCrumb />

          {/* 用户信息：头像 + 姓名（从 Store 读取） */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              {userInfo?.avatar ? (
                <img
                  src={userInfo.avatar}
                  alt={displayName}
                  className="h-8 w-8 rounded-full object-cover"
                />
              ) : (
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-sm font-medium text-white">
                  {displayName.charAt(0).toUpperCase()}
                </span>
              )}
              <span className="max-w-32 truncate text-sm text-slate-700">{displayName}</span>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              className="flex items-center gap-1.5 rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-100"
            >
              <LogOut size={14} />
              退出登录
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-auto p-4">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
