/**
 * Empty / Loading / PageContainer 单元测试（纯展示组件）
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import Empty from './Empty'
import Loading from './Loading'
import PageContainer from './PageContainer'

afterEach(cleanup)

describe('Empty', () => {
  it('默认文案与自定义文案', () => {
    const { rerender } = render(<Empty />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    rerender(<Empty description="没有匹配的记录" />)
    expect(screen.getByText('没有匹配的记录')).toBeInTheDocument()
  })

  it('渲染额外操作区', () => {
    render(
      <Empty>
        <button type="button">重新加载</button>
      </Empty>,
    )
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
  })
})

describe('Loading', () => {
  it('有文案时渲染文案与 spinner', () => {
    const { container } = render(<Loading text="加载中" />)
    expect(screen.getByText('加载中')).toBeInTheDocument()
    expect(container.querySelector('.animate-spin')).not.toBeNull()
  })

  it('无文案时不渲染文案', () => {
    render(<Loading />)
    expect(screen.queryByText('加载中')).toBeNull()
  })
})

describe('PageContainer', () => {
  it('渲染标题 / 说明 / 操作区 / 内容', () => {
    render(
      <PageContainer
        title="用户列表"
        description="管理后台用户"
        actions={<button type="button">新增</button>}
      >
        <div>表格内容</div>
      </PageContainer>,
    )
    expect(screen.getByText('用户列表')).toBeInTheDocument()
    expect(screen.getByText('管理后台用户')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新增' })).toBeInTheDocument()
    expect(screen.getByText('表格内容')).toBeInTheDocument()
  })

  it('无说明 / 无操作区时不渲染对应区域', () => {
    render(<PageContainer title="仅标题">内容</PageContainer>)
    expect(screen.queryByRole('button')).toBeNull()
  })
})
