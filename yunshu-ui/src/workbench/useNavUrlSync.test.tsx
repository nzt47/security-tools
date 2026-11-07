/**
 * 深链冷启动：地址栏里的 `?panel=`（以及紧随其后的 `tool=`）必须还在
 * ------------------------------------------------
 * 【为什么单开一条】`#/workbench?panel=tools&tool=x` 直接粘贴打开时，曾经落到
 * `/chat#/workbench`（search 整段被抹掉），而**应用内点击导航完全正常** ——
 * 症状只在"冷启动"出现，纯函数测试（hubNav.search.test.ts）抓不到。
 *
 * 根因不在本 hook，而在服务端入口：`app_server.index()` 原来是
 * `redirect("/chat#/workbench")`，Location 自带 fragment ⇒ 浏览器**丢弃**原 fragment。
 * 修法是把 fragment 让给浏览器带过去（Location 只写 `/chat`），
 * 对应的后端断言在 `tests/unit/test_root_redirect_deeplink.py`。
 *
 * 本用例守的是"浏览器把原 fragment 带过来之后"这一半：hook 挂载时**不得**
 * 用默认选中项把深链改写成 `panel=session`，也不得把 `tool=` 洗掉。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { useNavUrlSync } from './useNavUrlSync'
import { useWorkbenchNav } from './navStore'

/** 只挂 hook 的探针：把当时生效的导航 key 暴露到 DOM，便于断言"状态也跟上了" */
function Probe(): JSX.Element {
  useNavUrlSync()
  const activeKey = useWorkbenchNav((s) => s.activeKey)
  return <div data-testid="nav-key">{activeKey}</div>
}

const ORIGIN = 'http://localhost:3000/chat'

describe('深链冷启动：?panel= 与附加参数不被抹掉', () => {
  beforeEach(() => {
    // Zustand 是模块级单例：每条用例先把状态压回默认，避免互相污染
    useWorkbenchNav.setState({ activeKey: useWorkbenchNav.getState().activeKey })
    window.history.replaceState(null, '', ORIGIN)
  })

  afterEach(() => {
    cleanup()
    window.history.replaceState(null, '', ORIGIN)
  })

  it('工具页深链：panel=tools 与 tool=<名字> 都留在地址栏', async () => {
    window.history.replaceState(null, '', ORIGIN + '#/workbench?panel=tools&tool=web_search')

    const { getByTestId } = render(<Probe />)
    await Promise.resolve()

    const hash = window.location.hash
    expect(hash).toContain('panel=tools')
    expect(hash).toContain('tool=web_search')
    expect(hash).not.toContain('panel=session')
    expect(getByTestId('nav-key').textContent).toBe('tools')
  })

  it('治理面板深链：panel=governance/approvals 同样保留', async () => {
    window.history.replaceState(null, '', ORIGIN + '#/workbench?panel=governance%2Fapprovals')

    const { getByTestId } = render(<Probe />)
    await Promise.resolve()

    expect(decodeURIComponent(window.location.hash)).toContain('panel=governance/approvals')
    expect(getByTestId('nav-key').textContent).toBe('governance/approvals')
  })
})
