/**
 * 统一工作台导航状态（Zustand）
 * ------------------------------------------------
 * 管理当前选中的导航项；ContentPanel 据此渲染对应功能页。
 * 导航选中项不做持久化（会话级状态，刷新回默认）。
 */
import { create } from 'zustand'
import { DEFAULT_NAV_KEY } from './hubNav'

interface WorkbenchNavState {
  /** 当前选中导航 key（如 'session'、'panorama/sensors'、'admin/users'） */
  activeKey: string
  setActiveKey: (key: string) => void
}

export const useWorkbenchNav = create<WorkbenchNavState>((set) => ({
  activeKey: DEFAULT_NAV_KEY,
  setActiveKey: (key) => set({ activeKey: key }),
}))
