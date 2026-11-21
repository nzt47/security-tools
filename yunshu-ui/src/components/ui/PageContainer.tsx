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
  /**
   * page（默认）= 独立路由的整页：h1 + 页面级 padding/宽度约束；
   * section = 被叶子页 Tab 嵌进来：标题降为 h2（text-ui 14px），
   *           不再套 p-6/max-w-6xl（否则与其它叶子比是双层内边距，且一页两个 h1）。
   * 【2026-11-21】账号与权限 / 消息与通道 合页后暴露：原来每个子页都自带 h1 + p-6。
   */
  level?: 'page' | 'section'
}

export default function PageContainer({ title, description, actions, children, level = 'page' }: PageContainerProps) {
  const section = level === 'section'
  return (
    <div className={section ? 'space-y-4' : 'mx-auto max-w-6xl space-y-5 p-6'}>
      <div className="flex items-start justify-between gap-4">
        <div>
          {section
            ? <h2 className="text-ui font-semibold text-foreground">{title}</h2>
            : <h1 className="text-title font-semibold text-foreground">{title}</h1>}
          {description && (
            <p className={section ? 'mt-1 text-caption text-muted-foreground' : 'mt-1 text-sm text-muted-foreground'}>
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}
