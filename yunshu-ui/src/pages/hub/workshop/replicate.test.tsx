/**
 * 装配车间 —— 可带走 bundle 导出面守卫（S5）
 * ------------------------------------------------------------------
 * 锁死三件事：
 *   1. 导出走**真实端点常量**（SUBAGENT_BUNDLE_BY_NAME），不是页面里裸写 /api；
 *   2. 成功时把后端 bundle 原样展示（JSON 可复制）；
 *   3. 空名不发请求；后端拒绝（如密钥闸 409）⇒ 显示错误、不显示 JSON。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'

function json(body: unknown, status = 200) {
  return {
    ok: status < 300,
    status,
    headers: { get: () => null },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

const BUNDLE = {
  schema_version: 1,
  bundle_id: 'bnd-abc123',
  generated_at: '2026-10-09T00:00:00+00:00',
  identity: { name: 'sa-1', tags: [], ttl_seconds: 0, context_window: 4096 },
  assembly: { role: {}, model: {}, memory: {}, tools: {}, permissions: [] },
  secrets: { refs: [] },
  entrypoint: { protocol: 'task_file-jsonl', argv_template: [] },
  runtime: { backend: 'inproc' },
}

const calls: string[] = []
const fetchMock = vi.fn(async (url: string) => {
  calls.push(String(url))
  if (String(url).includes('/bundle')) return json({ ok: true, bundle: BUNDLE })
  return json({ ok: false, error: 'unexpected' }, 500)
})
vi.stubGlobal('fetch', fetchMock)

const mod = await import('./replicate')
const WorkshopReplicate = mod.default

beforeEach(() => { calls.length = 0; fetchMock.mockClear() })
afterEach(cleanup)

describe('装配车间 · 可带走 bundle 导出面', () => {
  it('填名导出 ⇒ 调真实端点并展示 JSON', async () => {
    render(<WorkshopReplicate />)
    fireEvent.change(screen.getByTestId('replicate-name'), { target: { value: 'sa-1' } })
    fireEvent.click(screen.getByTestId('replicate-export'))
    await waitFor(() => expect(screen.getByTestId('replicate-json')).toBeInTheDocument())
    expect(calls.some((u) => u === '/api/subagent/sa-1/bundle')).toBe(true)
    expect(screen.getByTestId('replicate-json').textContent).toContain('bnd-abc123')
  })

  it('空名不导出，给出错误且不发请求', () => {
    render(<WorkshopReplicate />)
    fireEvent.click(screen.getByTestId('replicate-export'))
    expect(screen.getByTestId('replicate-error')).toHaveTextContent('请先填写')
    expect(calls).toEqual([])
  })

  it('后端拒绝 ⇒ 显示错误而不显示 JSON', async () => {
    fetchMock.mockImplementationOnce(async () =>
      json({ ok: false, error: 'E_MANIFEST_SECRET_LEAK: 检出密钥' }, 409))
    render(<WorkshopReplicate />)
    fireEvent.change(screen.getByTestId('replicate-name'), { target: { value: 'leaky' } })
    fireEvent.click(screen.getByTestId('replicate-export'))
    await waitFor(() =>
      expect(screen.getByTestId('replicate-error')).toHaveTextContent('E_MANIFEST_SECRET_LEAK'))
    expect(screen.queryByTestId('replicate-json')).toBeNull()
  })

  it('页面如实标注导入与离线运行未做', () => {
    render(<WorkshopReplicate />)
    expect(screen.getAllByText(/界面未做/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/离线运行/).length).toBeGreaterThan(0)
  })
})
