/**
 * MainLayout —— 主布局：左侧 Sidebar + 顶部 Header + 中间内容区（Outlet）
 * 登录后初始化：挂载时若无 userInfo 则拉取当前用户信息；
 * 加载期间展示全屏骨架屏（防闪烁），拉取失败（如 401）则登出并跳回登录页。
 */
import { useEffect, useRef, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { Check, ChevronDown, FlaskConical, LogOut } from 'lucide-react'
import { useUserStore } from '@/store/userStore'
import { logger } from '@/utils/logger'
import { safeGetLocalStorage } from '@/utils/storage'
import { DASHBOARD_MOCK_ERROR_KEY } from '@/api/dashboard'
import Sidebar from '@/components/Sidebar'
import BreadCrumb from '@/components/BreadCrumb'

/** Dashboard 接口错误模拟场景选项（值对应 devMock 的 mock_error 参数） */
const DASHBOARD_MOCK_OPTIONS = [
  { value: '', label: '正常数据', desc: '真实后端返回' },
  { value: 'business', label: '业务错误(500)', desc: '拦截器 Toast + 空态' },
  { value: 'empty', label: '空数据', desc: 'data 为 null' },
  { value: 'invalid', label: '畸形数据', desc: '字段缺失 / 类型错误' },
]

/** 仅 dev：Dashboard 接口错误模拟场景下拉。写入 localStorage 后刷新页面，
 *  Dashboard 请求时读取并附加 mock_error 参数，由 devMock 拦截返回对应场景 */
function MockScenarioMenu() {
  const [open, setOpen] = useState(false)
  // 【SSR 兼容】渲染期读取 localStorage 经 safeGetLocalStorage（含 window 守卫）：
  // SSR 服务端（Node）无 window/localStorage，守卫后回退默认值（未选中），避免崩溃与 hydration 不一致
  const current = safeGetLocalStorage(DASHBOARD_MOCK_ERROR_KEY) ?? ''

  const handleSelect = (value: string) => {
    setOpen(false)
    if (value) localStorage.setItem(DASHBOARD_MOCK_ERROR_KEY, value)
    else localStorage.removeItem(DASHBOARD_MOCK_ERROR_KEY)
    // devMock 按请求参数拦截，刷新让 Dashboard 重新发起请求
    window.location.reload()
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="切换 Dashboard 接口错误模拟场景（仅开发环境）"
        className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors ${
          current
            ? 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
            : 'border-slate-200 text-slate-600 hover:bg-slate-100'
        }`}
      >
        <FlaskConical size={14} />
        接口场景
        <ChevronDown size={12} />
      </button>

      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-20 mt-1 w-60 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
            {DASHBOARD_MOCK_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => handleSelect(opt.value)}
                className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors hover:bg-slate-50 ${
                  current === opt.value ? 'text-amber-600' : 'text-slate-700'
                }`}
              >
                <span className="flex flex-col">
                  <span>{opt.label}</span>
                  <span className="text-xs text-slate-400">{opt.desc}</span>
                </span>
                {current === opt.value && <Check size={14} />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

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
      // 已存在用户信息（登录页 setUserInfo / 持久化恢复）与菜单树则跳过拉取
      if (useUserStore.getState().userInfo && useUserStore.getState().menus) {
        logger.info('[MainLayout] 已存在 userInfo 与菜单，跳过登录后初始化拉取')
        return
      }
      logger.info('[MainLayout] 未检测到 userInfo/菜单，开始拉取（骨架屏加载中）')
      setInitializing(true)
      try {
        // 并行拉取缺失项：用户信息 + 后端菜单树（方案2：菜单由后端下发）
        const state = useUserStore.getState()
        const tasks: Promise<unknown>[] = []
        if (!state.userInfo) tasks.push(state.fetchUserInfo())
        if (!state.menus) tasks.push(state.fetchMenus())
        await Promise.all(tasks)
        logger.info('[MainLayout] 用户信息与菜单拉取成功，退出骨架屏')
      } catch (err) {
        // 拉取失败（典型 401）：会话失效，登出并回登录页
        logger.warn('[MainLayout] 初始化拉取失败，执行登出并跳转登录页', err)
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
            {/* 仅 dev：接口错误模拟场景下拉（生产构建不含此代码） */}
            {import.meta.env.DEV && <MockScenarioMenu />}
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
