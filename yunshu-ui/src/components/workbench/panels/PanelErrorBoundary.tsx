/**
 * PanelErrorBoundary —— 工作台主内容区错误边界
 * ------------------------------------------------------------------
 * 【为什么必须有】ContentPanel 此前只有 Suspense，没有错误边界：任一功能页在**渲染期**抛错，
 * React 会卸载整棵子树 ⇒ 主内容区**整块空白**，用户无法区分"坏了"与"还没加载"。
 *   2026-10-15 实测案例：「系统管理 · 仪表盘」因接口结构与页面不符抛
 *   TypeError（data.stats 为 undefined）⇒ 打开即是空白页。
 * 这里把这类失败变成一条**可读的错误卡**（消息 + 重试），并在切换导航项时自动复位。
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

interface Props {
  children: ReactNode
  /** 变化时自动复位（如切换导航项 ⇒ resetKey=activeKey） */
  resetKey?: string
}

interface State {
  error: Error | null
}

export default class PanelErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[workbench] 面板渲染异常（已拦截）:', error, info.componentStack)
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="m-6 rounded-xl border border-red-900/60 bg-red-950/30 p-5">
        <div className="flex items-center gap-2 text-sm font-medium text-red-300">
          <AlertTriangle size={16} />
          该面板渲染失败（已拦截，避免整块空白）
        </div>
        <pre className="mt-3 max-h-40 overflow-auto rounded bg-slate-950/60 p-3 text-[11px] text-red-200">
          {String(error.message || error)}
        </pre>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="mt-3 flex items-center gap-1.5 rounded-md border border-red-800 px-3 py-1.5 text-[11px] text-red-200 hover:bg-red-900/40"
        >
          <RotateCcw size={12} /> 重试
        </button>
      </div>
    )
  }
}
