/**
 * Table 单元测试
 * - 列头 / 单元格渲染（缺省取值 + 自定义 render）
 * - loading 显示加载态；空数据显示空态
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import Table, { type TableColumn } from './Table'

afterEach(cleanup)

interface Row {
  name: string
  age: number
  role?: string
}

const columns: TableColumn<Row>[] = [
  { key: 'name', header: '名称' },
  { key: 'age', header: '年龄', align: 'center' },
  { key: 'role', header: '角色', render: (r) => r.role ?? '—' },
]

describe('Table', () => {
  it('渲染列头与数据行（缺省取值 + 自定义 render）', () => {
    render(
      <Table
        columns={columns}
        dataSource={[{ name: '张三', age: 30, role: 'admin' }]}
        rowKey={(r) => r.name}
      />,
    )
    expect(screen.getByText('名称')).toBeInTheDocument()
    expect(screen.getByText('年龄')).toBeInTheDocument()
    expect(screen.getByText('张三')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()
  })

  it('render 未提供时缺省回退文案', () => {
    render(
      <Table
        columns={columns}
        dataSource={[{ name: '李四', age: 25 }]}
        rowKey={(r) => r.name}
      />,
    )
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('loading 时数据区显示加载态', () => {
    const { container } = render(
      <Table columns={columns} dataSource={[]} loading rowKey={(r) => r.name} />,
    )
    expect(container.querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByText('暂无数据')).toBeNull()
  })

  it('空数据且非 loading 时显示空态文案', () => {
    render(<Table columns={columns} dataSource={[]} rowKey={(r) => r.name} />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
  })

  it('支持自定义空态文案', () => {
    render(
      <Table
        columns={columns}
        dataSource={[]}
        rowKey={(r) => r.name}
        emptyText="没有匹配的用户"
      />,
    )
    expect(screen.getByText('没有匹配的用户')).toBeInTheDocument()
  })
})
