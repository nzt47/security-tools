/**
 * useNavUrlSync —— 工作台导航状态 ⇄ 地址栏 双向同步（2026-09-22）
 * ------------------------------------------------
 * 【为什么要有】导航选中项原先只存在 Zustand 内存里（`navStore` 有意不做持久化），
 * 于是**没有任何办法把"当前看到的面板"告诉别人或存成书签**。操作者要打开
 * 「治理面板 → 审批收件箱」，只能靠一层层点（治理面板还在侧栏靠下的位置），
 * 实测出现过"找不到入口" ⇒ 审批单据挂单后长时间无人裁决。
 * 现在可以直链：`#/workbench?panel=governance/approvals`。
 *
 * 【为什么两个 effect 不会打架（不会互相触发成环）】
 *   · URL → 状态：只在 URL 里那个 key **合法**且与当前不同时写一次；
 *   · 状态 → URL：只在 URL 与当前状态不一致时 `replace` 一次。
 *   两条都以"两边已经一致"为终止条件 ⇒ 收敛。用 `replace` 而非 push，
 *   是为了不在浏览器历史里堆出一串"点一次导航多一条回退记录"的噪音。
 *   `navigate` 的依赖用 `location.search` 字符串而不是 `useSearchParams()` 返回的
 *   对象：后者每次渲染都是新引用，会让 effect 空转。
 */
import { useEffect } from 'react'
import { useInRouterContext, useLocation, useNavigate } from 'react-router-dom'
import { useWorkbenchNav } from './navStore'
import { navKeyFromSearch, searchForNavKey } from './hubNav'

export function useNavUrlSync(): void {
  const { search } = useLocation()
  const navigate = useNavigate()
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  const setActiveKey = useWorkbenchNav((s) => s.setActiveKey)

  // URL → 状态（首次进入 + 前进/后退）
  useEffect(() => {
    const fromUrl = navKeyFromSearch(search)
    if (!fromUrl) return
    // 读 store 快照而不是订阅 activeKey：否则"点击导航"也会把这条 effect 拉起来重跑
    if (fromUrl !== useWorkbenchNav.getState().activeKey) setActiveKey(fromUrl)
  }, [search, setActiveKey])

  // 状态 → URL（点击导航后把地址栏同步成可分享/可收藏的链接）
  useEffect(() => {
    if (!activeKey) return
    if (navKeyFromSearch(search) === activeKey) return
    const next = searchForNavKey(search, activeKey)
    if (next === search) return
    navigate({ search: next }, { replace: true })
  }, [activeKey, search, navigate])
}


/**
 * **只在 Router 上下文里挂载**的包装组件（WorkbenchApp 用）
 *
 * 【为什么必须有它，而不是让 WorkbenchApp 直接调 useNavUrlSync】
 *   既有的「src/components/Toaster.mount.test.tsx」会在**没有 Router** 的情况下直接渲染
 *   「<WorkbenchApp/>」（那是它测 Toaster 挂载点的最小宿主）。而 useLocation() 在 Router
 *   之外会**抛异常**（不是返回空值）⇒ 直接调用会让"工作台根能不能挂 Toaster"这条既有
 *   用例变红，且错误发生在渲染期（整棵子树都渲染不出来）。
 *   修法不是去改那条用例（它测的东西与路由无关），而是让"导航同步"这一能力**如实声明
 *   自己的前提**：只在 Router 内生效。React 的 hook 规则不允许在同一组件里条件调用
 *   useLocation，故用 useInRouterContext() 在**父组件**判断、在**子组件**内调用 hooks。
 */
export function NavUrlSync(): null {
  useNavUrlSync()
  return null
}
