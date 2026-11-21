/**
 * 身份提示词面板 · 「分身角色方案」区（2026-11-21）
 * ------------------------------------------------------------------
 * 锁：按角色类型分组展示多套提示词、切换角色、复制到剪贴板、没有方案时不渲染。
 * 这一块是**只读参考**（不写分身），所以断言只到"能看、能选、能复制"。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import IdentityPromptPanel from './IdentityPromptPanel'
import type { IdentityRow, IdentitySummary, RolePromptSetGroup } from './identityPrompt'

const ROWS: IdentityRow[] = [{
  key: 'persona', label: '身份设定', description: '你是谁', customContent: '',
  enabled: true, editable: true, estimate: 12, range: '10~20', note: '', tokenLimit: 0,
  emitted: true, emitOrder: 1, emitStage: 'system_prompt',
} as unknown as IdentityRow]

const SUMMARY: IdentitySummary = {
  total_enabled_tokens: 12, total_disabled_count: 0, grand_total: 20,
}

const ROLE_SETS: RolePromptSetGroup[] = [
  {
    role_id: 'generic_readonly',
    role_title: '通用只读执行体',
    role_note: '什么都不确定时的默认角色',
    sets: [
      { id: 'generic_readonly.standard', title: '标准只读', scene: '通用委派', body: '标准只读正文', chars: 5 },
      { id: 'generic_readonly.triage', title: '只读排障', scene: '线上定位', body: '排障正文', chars: 4 },
    ],
  },
  {
    role_id: 'code_review',
    role_title: '代码审查',
    sets: [
      { id: 'code_review.standard', title: '标准审查', scene: '审 diff', body: '审查正文', chars: 4 },
    ],
  },
]

function props(over: Record<string, unknown> = {}) {
  return {
    rows: ROWS, summary: SUMMARY, loading: false, saving: false, applying: false,
    dirty: false, error: '', msg: '',
    onToggle: vi.fn(), onEditContent: vi.fn(), onSave: vi.fn(), onApply: vi.fn(),
    onReset: vi.fn(), onReload: vi.fn(),
    ...over,
  }
}

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('IdentityPromptPanel · 分身角色方案', () => {
  it('按角色类型列出，默认展示第一类的全部方案', () => {
    render(<IdentityPromptPanel {...props({ roleSets: ROLE_SETS })} />)
    expect(screen.getByText('分身角色方案（按角色类型）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '通用只读执行体' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '代码审查' })).toBeInTheDocument()
    expect(screen.getByText('标准只读')).toBeInTheDocument()
    expect(screen.getByText('只读排障')).toBeInTheDocument()
    // 另一角色的方案此时不展示
    expect(screen.queryByText('标准审查')).toBeNull()
    expect(screen.getByLabelText('标准只读 角色提示词正文')).toHaveValue('标准只读正文')
  })

  it('切换角色类型 ⇒ 只展示该类型的方案', () => {
    render(<IdentityPromptPanel {...props({ roleSets: ROLE_SETS })} />)
    fireEvent.click(screen.getByRole('button', { name: '代码审查' }))
    expect(screen.getByText('标准审查')).toBeInTheDocument()
    expect(screen.queryByText('标准只读')).toBeNull()
    expect(screen.getByRole('button', { name: '代码审查' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('复制 ⇒ 把整段正文写进剪贴板，并回显已复制', async () => {
    render(<IdentityPromptPanel {...props({ roleSets: ROLE_SETS })} />)
    const card = screen.getByLabelText('标准只读 角色提示词正文').closest('[data-cp-role-set]') as HTMLElement
    fireEvent.click(card.querySelector('button') as HTMLButtonElement)
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('标准只读正文'))
    await waitFor(() => expect(screen.getByText('已复制')).toBeInTheDocument())
  })

  it('没有方案（接口降级/未取到）⇒ 整块不渲染，身份提示词照常', () => {
    render(<IdentityPromptPanel {...props({ roleSets: [] })} />)
    expect(screen.queryByText('分身角色方案（按角色类型）')).toBeNull()
    expect(screen.getByText('身份提示词（系统提示词 · 线上配置）')).toBeInTheDocument()
  })
})
