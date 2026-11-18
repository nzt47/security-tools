/**
 * Table 单元测试
 * - 列头 / 单元格渲染（缺省取值 + 自定义 render）
 * - loading 显示加载态；空数据显示空态
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import Table, { type TableColumn } from './Table'
import { COL_RESIZER_LABEL } from '../ColResizer'

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

  it('columns.title 与 header 等价（兼容 hub DataTable 的旧字段名）', () => {
    render(
      <Table
        columns={[{ key: 'name', title: '标题别名' }]}
        dataSource={[{ name: '张三' }]}
        rowKey={(r) => r.name}
      />,
    )
    expect(screen.getByText('标题别名')).toBeInTheDocument()
  })

  it('rowProps 只合到命中的数据行（深链锚点）', () => {
    const { container } = render(
      <Table
        columns={columns}
        dataSource={[{ name: '张三', age: 30 }, { name: '李四', age: 25 }]}
        rowKey={(r) => r.name}
        rowProps={(r) => (r.name === '李四' ? { 'data-focus-row': r.name } : {})}
      />,
    )
    expect(container.querySelector('[data-focus-row="李四"]')).not.toBeNull()
    expect(container.querySelector('[data-focus-row="张三"]')).toBeNull()
  })

  it('resizable：colgroup 宽度 / colWidths 覆盖 / 拖动手柄', () => {
    render(
      <Table
        columns={columns}
        dataSource={[{ name: '张三', age: 30, role: 'admin' }]}
        rowKey={(r) => r.name}
        resizable
        colStore="t.cols.test"
        colWidths={{ age: 90 }}
      />,
    )
    const cols = Array.from(document.querySelectorAll('col'))
    expect(cols).toHaveLength(3)
    expect(cols[0].style.width).toBe('240px')
    expect(cols[1].style.width).toBe('90px')
    expect(screen.getAllByRole('separator', { name: COL_RESIZER_LABEL })).toHaveLength(3)
  })

  it('resizable：拖动后宽度落 localStorage（松手才写）', () => {
    localStorage.removeItem('t.cols.drag')
    render(
      <Table
        columns={columns}
        dataSource={[{ name: '张三', age: 30, role: 'admin' }]}
        rowKey={(r) => r.name}
        resizable
        colStore="t.cols.drag"
      />,
    )
    const handle = screen.getAllByRole('separator', { name: COL_RESIZER_LABEL })[0]
    fireEvent.mouseDown(handle, { clientX: 100 })
    expect(localStorage.getItem('t.cols.drag')).toBeNull()
    fireEvent.mouseMove(window, { clientX: 140 })
    fireEvent.mouseUp(window)
    expect(JSON.parse(localStorage.getItem('t.cols.drag') ?? '{}').name).toBe(280)
    localStorage.removeItem('t.cols.drag')
  })
})
