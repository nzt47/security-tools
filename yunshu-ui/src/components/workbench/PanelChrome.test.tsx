/**
 * 工作台界面 chrome（2026-11-15 B 步）—— 6px 抓取条 / ⋮ 菜单 / 顶栏开关 / 右栏抽屉
 * ------------------------------------------------------------------
 * 本轮验收清单（§11.122 D）里能自动化断言的都落在这里：
 *   ① 面板标题栏整条去掉 ⇒ 工具条里只剩抓取条，且仍是库认可的 drag source（.draggable）；
 *   ② 三动作（独立窗口 / 放大 / 关闭）走 ⋮ 菜单，语义与库默认按钮一致；
 *   ③ 顶栏「导航 / 右栏」开关能真的摘除/插回面板（修"关掉后只能重置布局"）；
 *   ④ 右栏默认收起、点开是 320px 覆盖式抽屉（不进布局树 ⇒ 不挤压主内容），开合状态持久化。
 *
 * 说明：拖拽拆分 / 拖到边缘分离是 HTML5 DnD + Electron IPC 的行为，jsdom 里没有原生拖放，
 * 这里用**结构断言**守住前置条件（自定义 toolbar 仍是 drag source、工具栏高度不为 0），
 * 真实的拖拽/分离在 Electron 手工验收（见交接文档）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import WorkbenchApp from '../../WorkbenchApp'
import { DEFAULT_LAYOUT, LAYOUT_STORAGE_KEY, PANEL_TITLES } from '../../lib/mosaic'
import { useLayoutStore } from '../../stores/useLayoutStore'

/** 收集布局树里的全部叶子面板 ID */
function leaves(node: unknown): string[] {
  if (typeof node === 'string') return [node]
  if (node && typeof node === 'object') {
    const n = node as { type?: string; children?: unknown[]; tabs?: string[] }
    if (n.type === 'split' && Array.isArray(n.children)) return n.children.flatMap(leaves)
    if (n.type === 'tabs' && Array.isArray(n.tabs)) return [...n.tabs]
  }
  return []
}

/** 会话列表 / 抽屉里的各种 GET 都吃这一份响应（缺的字段由调用方按空值处理） */
function stubFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ sessions: [], current_id: null, code: 0, data: {}, total: 0, items: [] }),
    })),
  )
}

/**
 * 挂载工作台并**把首屏的异步副作用冲干净**（会话列表 / 报警器 / 浮层都是挂载后 fetch）。
 * 直接 render 会让这些 setState 落在 act 之外，Vitest 输出一堆 "not wrapped in act" 警告
 * （不影响断言，但会淹没真正的失败信息，且全量跑时容易与新用例串味）。
 */
async function renderApp() {
  let result: ReturnType<typeof render> | null = null;
  await act(async () => {
    result = render(<WorkbenchApp />);
  });
  return result as ReturnType<typeof render>;
}

/** 面板右上角 ⋮ 的触发器（aria-label = "<面板名> 面板菜单"） */
function openMenu(panelTitle: string) {
  fireEvent.click(screen.getByLabelText(`${panelTitle} 面板菜单`))
  return screen.getByRole('menu', { name: `${panelTitle} 面板操作` })
}

beforeEach(() => {
  stubFetch()
  localStorage.clear()
  useLayoutStore.setState({
    layout: DEFAULT_LAYOUT,
    messages: [],
    thinking: [],
    streaming: false,
    activeStreamId: null,
    activeSessionId: null,
    highlightMsgId: null,
    pendingApproval: null,
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  delete (window as { electronAPI?: unknown }).electronAPI
})

describe('面板标题栏 → 顶部抓手（细条 0 高）', () => {
  it('每个可见面板只有一条抓手，工具条里不再有标题文字', async () => {
    const { container } = await renderApp()

    expect(screen.getAllByTestId('wb-panel-grip')).toHaveLength(3) // nav + chat + 右坞（2026-11-19 起默认打开）
    const toolbars = Array.from(container.querySelectorAll('.mosaic-window > .mosaic-window-toolbar'))
    expect(toolbars).toHaveLength(3)

    for (const toolbar of toolbars) {
      // 标题栏整条去掉 ⇒ 工具条自身没有任何文字
      expect(toolbar.textContent).toBe('')
      // 库只在"允许拖拽"时加 .draggable；它同时是"把 toolbar 包成 drag source"的判据
      expect(toolbar.className).toContain('draggable')
      // 工具条里唯一的节点就是我们返回的抓取条 ⇒ 拖拽命中区就是这 6px
      expect(toolbar.children).toHaveLength(1)
      expect(toolbar.querySelectorAll('[data-testid="wb-panel-grip"]')).toHaveLength(1)
    }
  })
})

describe('⋮ 菜单：三动作语义与库默认按钮一致', () => {
  it('放大 = 该面板撑到 70%（与 ExpandButton 同一实现）', async () => {
    await renderApp()
    openMenu(PANEL_TITLES.chat)
    expect(screen.getByRole('menuitem', { name: '放大' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: '放大' }))

    const layout = useLayoutStore.getState().layout as { splitPercentages: number[] }
    // chat 是根横向 split 的第 2 个子节点 ⇒ 它被撑到 70，导航退到 30
    expect(layout.splitPercentages[1]).toBe(70)
  })

  it('关闭 = 从布局树摘除；顶栏开关能把它再打开（修"关掉只能重置布局"）', async () => {
    await renderApp()
    // 关掉主内容：默认顶栏没有「主内容」开关，关掉之后才出现
    expect(screen.queryByRole('button', { name: '主内容' })).toBeNull()
    openMenu(PANEL_TITLES.chat)
    fireEvent.click(screen.getByRole('menuitem', { name: '关闭' }))

    expect(leaves(useLayoutStore.getState().layout)).toEqual(['nav', 'code'])
    const restore = screen.getByRole('button', { name: '主内容' })
    expect(restore).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(restore)
    expect(leaves(useLayoutStore.getState().layout)).toEqual(['nav', 'chat', 'code'])
  })

  it('独立窗口 = 走 Electron IPC（含面板 id / 路由），成功后从布局摘除', async () => {
    type DetachReq = { panelId: string; route: string; title: string }
    // 显式声明入参类型：否则 mock.calls[0][0] 的元组长度为 0（tsc -b 会报 TS2493）
    const detachMock = vi.fn<(req: DetachReq) => Promise<number>>(async () => 7)
    // 注：对象字面量里不要用 shorthand（本文件实测会触发 TDZ 报错）
    ;(window as { electronAPI?: unknown }).electronAPI = {
      detachPanel: detachMock,
      getWindowMeta: vi.fn(),
      getInitialState: vi.fn(),
      broadcastState: vi.fn(),
      onStateSync: vi.fn(() => () => {}),
      pickWorkspaceDirectory: vi.fn(),
    }
    await renderApp()

    const menu = openMenu(PANEL_TITLES.chat)
    fireEvent.click(screen.getByRole('button', { name: '独立窗口' }))
    expect(menu).not.toBeInTheDocument() // 点了就收起
    await act(async () => {})

    expect(detachMock).toHaveBeenCalledTimes(1)
    const req = detachMock.mock.calls[0][0]
    expect(req.panelId).toBe('chat')
    expect(req.route).toBe('/detached/chat')
    expect(req.title).toContain('独立窗口')
    // 摘除后主内容不在树里 ⇒ 顶栏出现「主内容」开关兜底（右坞仍在）
    expect(leaves(useLayoutStore.getState().layout)).toEqual(['nav', 'code'])
  })
})

describe('顶栏「导航 / 右栏」开关', () => {
  it('「导航」摘除后能原位置插回，比例不丢', async () => {
    await renderApp()
    const toggle = screen.getByRole('button', { name: '导航' })
    expect(toggle).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(toggle)
    // 三栏时摘掉导航 ⇒ 剩下 chat | code 一列（不是整棵树塌成 'chat'）
    const mid = useLayoutStore.getState().layout as { children: string[] }
    expect(mid.children).toEqual(['chat', 'code'])
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getAllByTestId('wb-panel-grip')).toHaveLength(2)

    fireEvent.click(toggle)
    const back = useLayoutStore.getState().layout as { children: string[]; splitPercentages: number[] }
    expect(back.children).toEqual(['nav', 'chat', 'code'])
    expect(back.splitPercentages).toHaveLength(3)
    expect(back.splitPercentages[0]).toBe(11) // 导航回到默认 11%
  })

  it('右栏默认打开（终端 · 实时活动）；顶栏开关可关掉，再点插回最右一列', async () => {
    await renderApp()
    // 2026-11-19 起默认打开：树里有 code，三块面板
    expect(leaves(useLayoutStore.getState().layout)).toEqual(['nav', 'chat', 'code'])
    expect(screen.getAllByTestId('wb-panel-grip')).toHaveLength(3)
    const toggle = screen.getByRole('button', { name: '右栏' })
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    // 右坞默认落在「终端」Tab，且默认是「实时活动」视图
    expect(screen.getByRole('tab', { name: /终端/ })).toHaveAttribute('aria-selected', 'true')

    fireEvent.click(toggle)
    expect(leaves(useLayoutStore.getState().layout)).toEqual(['nav', 'chat'])
    expect(toggle).toHaveAttribute('aria-pressed', 'false')

    // 再点：插到最右的**真 mosaic 列**（主内容收缩，不是盖在上面）
    fireEvent.click(toggle)
    const layout = useLayoutStore.getState().layout as {
      children: string[]
      splitPercentages: number[]
    }
    expect(layout.children).toEqual(['nav', 'chat', 'code'])
    expect(layout.splitPercentages).toHaveLength(3)
    // 关掉再打开：关的时候剩余两栏按比例放大（11/75 → 14.67），再插回时导航保持 14.67
    expect(layout.splitPercentages[0]).toBeCloseTo((11 / 75) * 100, 3)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByTestId('wb-panel-grip')).toHaveLength(3)
    // 持久化：状态就在布局树里（同一个 localStorage 键）
    expect(localStorage.getItem(LAYOUT_STORAGE_KEY)).toContain('"code"')
  })
})
