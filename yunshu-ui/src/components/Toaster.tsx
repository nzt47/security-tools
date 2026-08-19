/**
 * 全局 Toast（Tailwind 样式，淡入淡出）
 * - 用法：toast.success('保存成功') / toast.error('出错了') / toast.info('提示')
 * - <Toaster /> 在入口 main.tsx 挂载一次，全局唯一
 * - 模块级单例：非 React 模块（如 request.ts 拦截器）也可直接调用 toast
 */
// 同文件同时导出组件与 toast 工具对象：react-refresh 仅对组件热更新，此告警不适用
/* eslint-disable react-refresh/only-export-components */
import { useEffect, useState } from 'react'
import './toast.css'

export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  type: ToastType
  message: string
}

/** 显示时长（ms），淡出动画在 2.7s 开始，与此值保持同步（见 toast.css） */
const DURATION = 3000

let items: ToastItem[] = []
let listeners: Array<(list: ToastItem[]) => void> = []
let nextId = 0

function emit(): void {
  listeners.forEach((l) => l([...items]))
}

function push(type: ToastType, message: string): void {
  // 防重复弹窗：同一 type+message 的 Toast 已在展示时不重复入列。
  // 弱网/React StrictMode 等导致的并发重复请求会触发多次同文案错误，只提示一次即可。
  if (items.some((t) => t.type === type && t.message === message)) {
    return
  }
  const id = ++nextId
  items = [...items, { id, type, message }]
  emit()
  window.setTimeout(() => {
    items = items.filter((t) => t.id !== id)
    emit()
  }, DURATION)
}

export const toast = {
  success: (message: string) => push('success', message),
  error: (message: string) => push('error', message),
  info: (message: string) => push('info', message),
}

const TYPE_STYLE: Record<ToastType, { box: string; color: string }> = {
  success: { box: 'border-green-300 bg-green-50 text-green-800', color: '#16a34a' },
  error: { box: 'border-red-300 bg-red-50 text-red-800', color: '#dc2626' },
  info: { box: 'border-blue-300 bg-blue-50 text-blue-800', color: '#2563eb' },
}

function ToastIcon({ type }: { type: ToastType }) {
  const color = TYPE_STYLE[type].color
  if (type === 'success') {
    return (
      <svg viewBox="0 0 20 20" fill="none" stroke={color} strokeWidth="2" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 10l3 3 7-7" />
      </svg>
    )
  }
  if (type === 'error') {
    return (
      <svg viewBox="0 0 20 20" fill="none" stroke={color} strokeWidth="2" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l8 8M14 6l-8 8" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke={color} strokeWidth="2" className="h-4 w-4">
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 9v5M10 6v.01" />
    </svg>
  )
}

export default function Toaster() {
  const [list, setList] = useState<ToastItem[]>([])

  useEffect(() => {
    listeners.push(setList)
    setList([...items])
    return () => {
      listeners = listeners.filter((l) => l !== setList)
    }
  }, [])

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-[9999] flex w-80 flex-col gap-2">
      {list.map((t) => (
        <div
          key={t.id}
          role="alert"
          className={`toast flex items-start gap-2 rounded-lg border px-4 py-3 shadow-md ${TYPE_STYLE[t.type].box}`}
        >
          <span className="mt-0.5 shrink-0">
            <ToastIcon type={t.type} />
          </span>
          <span className="break-all text-sm leading-5">{t.message}</span>
        </div>
      ))}
    </div>
  )
}
