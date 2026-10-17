/**
 * useNavUrlSync —— 工作台导航状态 ⇄ 地址栏 双向同步（2026-09-22 建，2026-10-17 修）
 * ------------------------------------------------
 * 【为什么要有】导航选中项只存在 Zustand 内存里（navStore 有意不做持久化），
 * 于是没有任何办法把"当前看到的面板"告诉别人或存成书签。现在可以直链：
 * `#/workbench?panel=governance/approvals`。
 *
 * 【2026-10-17 修了什么】深链此前**一直不生效**：冷启动落在「会话任务」，地址栏还会被
 * 改写成 panel=session（实测 ?panel=governance/approvals 同样如此）。根因是同步逻辑
 * 只信 `useLocation().search`——本工作台挂在 HashRouter 下，路由 search 与 hash 里的
 * `?panel=...` 不同源，拿到的常是空串：于是「URL→状态」什么也不做，
 * 「状态→URL」反而把地址栏改写成默认页。
 *
 * 现在直接以 **window.location.hash** 为准（读与写都走它），并监听 hashchange：
 *   · 读：hash 里的 `?panel=` → navKeyFromSearch 归一（含 LEGACY_NAV_KEY_MAP）
 *   · 写：activeKey 变化 → searchForNavKey 生成新 search → history.replaceState
 * 不再依赖 react-router 的 location，因此不受路由嵌套/挂载顺序影响。
 *
 * 【为什么两个 effect 不会打架（不会互相触发成环）】
 *   · URL → 状态：只在 URL 里那个 key **合法**且与当前不同时写一次；
 *   · 状态 → URL：只在 URL 与当前状态不一致时 replace 一次。
 *   两条都以"两边已经一致"为终止条件 ⇒ 收敛。用 replace 而非 push，
 *   是为了不在浏览器历史里堆出"点一次导航多一条回退记录"的噪音。
 */
import { useEffect, useRef } from 'react'
import { useWorkbenchNav } from './navStore'
import { navKeyFromSearch, searchForNavKey } from './hubNav'

/** 当前 hash 里的 search 段（形如 '?panel=engine'；没有则空串） */
function hashSearch(): string {
  const raw = window.location.hash || ''
  const i = raw.indexOf('?')
  return i >= 0 ? raw.slice(i) : ''
}

/** 把新的 search 段写回 hash（保留路径；replaceState 不触发 hashchange，避免回环） */
function writeHashSearch(next: string): void {
  const raw = window.location.hash || ''
  const base = raw.split('?')[0] || '#/workbench'
  const target = base + next
  if (raw !== target) window.history.replaceState(null, '', target)
}

export function useNavUrlSync(): void {
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const setActiveKey = useWorkbenchNav((s) => s.setActiveKey)

  // URL → 状态（首次进入 + 前进/后退 + 手改地址栏）
  useEffect(() => {
    const apply = () => {
      const fromUrl = navKeyFromSearch(hashSearch())
      if (!fromUrl) return
      // 读 store 快照而不是订阅 activeKey：否则"点击导航"也会把这条 effect 拉起来重跑
      if (fromUrl !== useWorkbenchNav.getState().activeKey) setActiveKey(fromUrl)
    }
    apply()
    window.addEventListener('hashchange', apply)
    return () => window.removeEventListener('hashchange', apply)
  }, [setActiveKey])

  // 首次挂载时**不写 URL**：此时 activeKey 还是默认值（"会话任务"），
  // 若抢先写入会先把深链冲成 panel=session（旧实现正是这样把深链弄丢的）。
  // 地址栏的首屏对齐由上面的「URL → 状态」负责。
  const urlWriteArmed = useRef(false)

  // 状态 → URL（点击导航后把地址栏同步成可分享/可收藏的链接）
  useEffect(() => {
    if (!urlWriteArmed.current) {
      urlWriteArmed.current = true
      return
    }
    if (!activeKey) return
    const current = hashSearch()
    if (navKeyFromSearch(current) === activeKey) return
    const next = searchForNavKey(current, activeKey)
    if (next === current) return
    writeHashSearch(next)
  }, [activeKey])
}

/**
 * **只在 Router 上下文里挂载**的包装组件（WorkbenchApp 用）
 *
 * 【为什么还留着它】历史实现里本 hook 用了 useLocation()，在 Router 之外会抛异常
 * （不是返回空值），而 Toaster.mount.test.tsx 会在无 Router 的裸环境渲染 WorkbenchApp。
 * 2026-10-17 改为直接读写 window.location.hash 后已不依赖 Router；包装组件与
 * WorkbenchApp 的 inRouter 守卫保留（幂等、无副作用，也为将来换路由实现留口）。
 */
export function NavUrlSync(): null {
  useNavUrlSync()
  return null
}
