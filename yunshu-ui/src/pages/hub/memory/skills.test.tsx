/**
 * LLM 技能库（技能中心 · LLM 技能 Tab）—— 内置 persona 技能的「启用/停用」入口
 * ------------------------------------------------------------------
 * 【为什么单独锁这条】2026-10-19 把扩展市场里最后一个内置分组
 * （内置行为 = 随仓库分发的 persona 提示片段）撤掉，指路到本 Tab。
 * 撤组的前提是「这里真的能启停内置技能」—— 本测试就是那个前提的证据：
 *   · 行来自 `/api/skills`（installed 优先），enabled=false 时给「启用」；
 *   · 点击 = POST /api/skills/toggle {id}，成功后重拉列表；
 *   · 事实源是 SkillRegistry，界面不本地推导状态。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

const env = vi.hoisted(() => ({ getEnvelope: vi.fn() }))
vi.mock('@/api/envelope', () => ({ getEnvelope: env.getEnvelope }))

vi.mock('@/lib/callability', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchCapabilityManifest: vi.fn(async () => ({ runtime_skills: [], manifest: { skills: [], vocabulary: { mark: [] } } })),
}))

import { MemorySkillsTable } from './skills'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  env.getEnvelope.mockReset()
})

function setup(enabled: boolean) {
  env.getEnvelope.mockResolvedValue({
    installed: [{
      id: 'self_reflection',
      name: '自省反思',
      enabled,
      description: 'reflect after each turn',
      class_name: '交流与人格',
    }],
    available: [],
  })
  api.hubGet.mockResolvedValue({ ok: true, groups: [] })
}

describe('MemorySkillsTable · 内置 persona 技能的启停', () => {
  it('停用的内置技能给「启用」按钮，点击 POST /api/skills/toggle 并重拉', async () => {
    setup(false)
    api.hubPost.mockResolvedValue({ ok: true })
    render(<MemorySkillsTable />)

    expect(await screen.findByText('自省反思')).toBeInTheDocument()
    const before = env.getEnvelope.mock.calls.length

    const btn = screen.getByRole('button', { name: /启用/ })
    fireEvent.click(btn)

    await waitFor(() => {
      const call = api.hubPost.mock.calls.find((c) => c[0] === '/api/skills/toggle')
      expect(call, '点击启用必须发 toggle').toBeTruthy()
      expect(call?.[1]).toEqual({ id: 'self_reflection' })
    })
    // 启停后必须重拉（状态事实源在服务端，不在本地推导）
    await waitFor(() => expect(env.getEnvelope.mock.calls.length).toBeGreaterThan(before))
  })

  it('已启用的内置技能给「停用」按钮（不是"已在用就不给入口"）', async () => {
    setup(true)
    render(<MemorySkillsTable />)

    expect(await screen.findByText('自省反思')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /停用/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^启用$/ })).toBeNull()
  })
})
