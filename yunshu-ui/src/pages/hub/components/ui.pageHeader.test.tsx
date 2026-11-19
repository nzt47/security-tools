/**
 * 叶子页「标题 + Tab 条」组合与区块标题的字号契约（2026-11-19）
 *
 * 背景：Owner 报「能力获取/引擎 的标题落在 Tab 下面、TAB 的小标题字号不统一」。
 * 这两个组件把「谁在上面」和「用哪一档字号」从"各页自己拼"收回到一处：
 *   - PageTabsHeader：h1 = text-title（16）+ 副标题 text-caption（11）+ Tab 条在标题**下面**；
 *   - SectionTitle：h2 = text-ui（14）+ 说明 text-caption（11）（heading 阶梯 B）。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PageTabsHeader, SectionTitle, Tabs } from './ui'

describe('PageTabsHeader', () => {
  it('标题是 h1 + text-title，且排在 Tab 条前面（标题不会落到 Tab 下面）', () => {
    const { container } = render(
      <PageTabsHeader
        title="能力获取"
        hint="外来技能 / 文档 / 提示词"
        tabs={<Tabs aria-label="子模块" value="a" onChange={() => {}} items={[{ value: 'a', label: '甲' }]} />}
      />,
    )
    const h1 = screen.getByRole('heading', { level: 1, name: '能力获取' })
    expect(h1.className).toContain('text-title')
    const hint = screen.getByText('外来技能 / 文档 / 提示词')
    expect(hint.className).toContain('text-caption')
    const tablist = screen.getByRole('tablist', { name: '子模块' })
    // 标题在 Tab 条**上面**：DOM 文档序里 h1 必须先于 tablist
    expect(h1.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 同一屏只应有一个 h1（子模块不要再自带一个页级标题）
    expect(container.querySelectorAll('h1')).toHaveLength(1)
  })
})

describe('SectionTitle（TAB 内容 / 区块标题）', () => {
  it('level=2 渲染 h2 + text-ui（14），说明是 text-caption（11）', () => {
    render(
      <SectionTitle title="心跳监测" description="全维度健康检查与历史记录" dot={false} />,
    )
    const h2 = screen.getByRole('heading', { level: 2, name: '心跳监测' })
    expect(h2.className).toContain('text-ui')
    const desc = screen.getByText('全维度健康检查与历史记录')
    expect(desc.className).toContain('text-caption')
  })

  it('level 阶梯：1→text-title / 2→text-ui / 3→text-body', () => {
    const { container } = render(
      <SectionTitle level={3} title="小标题" dot={false} />,
    )
    expect(container.querySelector('h3')?.className).toContain('text-body')
  })
})
