/**
 * LoginLayout —— 登录页空白布局
 * 不含侧边栏/顶栏，仅提供一个干净的居中画布渲染子路由
 *
 * 挂载 <Toaster/>（缺陷 ①）：登录页/系统页操作的 toast.success/error 提示依赖
 * <Toaster/> 容器渲染；LoginLayout 是 /login 路由的宿主，登录失败/表单校验提示
 * 须在此可见（Toaster 为模块级单例，幂等，多布局挂载不会重复弹窗）。
 */
import { Outlet } from 'react-router-dom'
import Toaster from '@/components/Toaster'

export default function LoginLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100">
      <Outlet />
      <Toaster />
    </div>
  )
}
