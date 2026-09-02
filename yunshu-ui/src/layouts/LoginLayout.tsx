/**
 * LoginLayout —— 登录页空白布局
 * 不含侧边栏/顶栏，仅提供一个干净的居中画布渲染子路由
 */
import { Outlet } from 'react-router-dom'

export default function LoginLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100">
      <Outlet />
    </div>
  )
}
