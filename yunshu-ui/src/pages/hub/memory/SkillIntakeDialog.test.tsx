/**
 * 入轨助手 · 「同时刷新基线」默认关（2026-11-08 Owner 决定，R-1）
 * ------------------------------------------------------------------
 * 背景：2026-11-07 23:49 那次 UI 入轨，助手顺手把 capability_manifest / 盘点表 /
 * 3 处测试基线一起改了 —— 那份基线是「派生面哨兵」（必须等于干净 worktree 重派生的
 * 结果），自动改写等于绕过 review 挪哨兵。现在默认不刷，要刷必须显式勾。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import SkillIntakeDialog from './SkillIntakeDialog'

const ITEM = { skill_id: 'document-skill', tracked: false, errors: [], warnings: [], fixable: [], needs_human: [] }

let calls: { url: string; body?: Record<string, unknown> }[] = []

beforeEach(() => {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (String(url).endsWith('/intake/status')) {
      return { ok: true, json: async () => ({ ok: true, items: [ITEM] }) } as unknown as Response
    }
    return {
      ok: true,
      json: async () => ({ ok: true, meta_fixed: [], derived_changed: [], baseline_changes: [], fix_baselines: false }),
    } as unknown as Response
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('入轨助手 · 基线默认不刷', () => {
  it('默认不勾「同时刷新基线」，请求体里 fix_baselines=false', async () => {
    render(<SkillIntakeDialog open onClose={() => {}} />)
    const box = await screen.findByRole('checkbox', { name: '同时刷新基线' })
    expect((box as HTMLInputElement).checked).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '一键入轨' }))
    await waitFor(() => expect(calls.some((c) => c.url.includes('/document-skill/intake'))).toBe(true))
    const post = calls.find((c) => c.url.includes('/document-skill/intake'))!
    expect(post.body?.fix_baselines).toBe(false)
    expect(post.body?.execute).toBe(true)
  })

  it('勾上之后才传 fix_baselines=true，并在结果里如实说明', async () => {
    render(<SkillIntakeDialog open onClose={() => {}} />)
    const box = await screen.findByRole('checkbox', { name: '同时刷新基线' })
    fireEvent.click(box)
    expect((box as HTMLInputElement).checked).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '一键入轨' }))
    await waitFor(() => expect(calls.some((c) => c.url.includes('/document-skill/intake'))).toBe(true))
    expect(calls.find((c) => c.url.includes('/document-skill/intake'))!.body?.fix_baselines).toBe(true)
  })
})
