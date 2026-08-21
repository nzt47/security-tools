/**
 * PageContainer —— 页面骨架容器
 * 统一「标题 + 说明 + 操作区 + 内容」结构，系统管理页等标准页使用。
 */
import type { ReactNode } from 'react'

export interface PageContainerProps {
  /** 页面标题 */
  title: string
  /** 页面说明（标题下方次级文案），可选 */
  description?: string
  /** 头部操作区（如"新增"按钮），可选 */
  actions?: ReactNode
  children: ReactNode
}

export default function PageContainer({ title, description, actions, children }: PageContainerProps) {
  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-foreground">{title}</h1>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}
