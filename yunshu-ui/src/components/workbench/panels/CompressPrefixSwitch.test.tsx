/**
 * 压缩复用对话前缀 · 就地开关（与「手动压缩」同一行）
 * ------------------------------------------------------------------
 * 锁四件事：
 *   1. 读数来自开关中心同一份登记表（GET /api/cp/settings，按 key 取）；
 *   2. 切换只发 POST /api/cp/settings/<key> {value}（A 级、hot），成功后开关态立即翻转；
 *   3. 说明默认收起、点开能看到「开/关/自动回落」三段的真实数字（99.7% / 0-57,385 / 1.85 倍）；
 *   4. 不可编辑 / 后端 ok=false 都要如实上屏，不能静默。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@/lib/apiClient', () => ({ request: api.request }))

import { CompressPrefixSwitch, KEY } from './CompressPrefixSwitch'

const ITEM = {
  key: KEY,
  value: false,
  editable: true,
  source_label: '代码默认值',
  effect_label: '下次读取即生效',
}

function setup(item: Record<string, unknown> = ITEM) {
  api.request.mockImplementation(async (_url: string, opts?: { method?: string }) => {
    if (opts?.method === 'POST') return { ok: true, applied: true, receipt: { detail: '已写入进程环境变量（热生效）' } }
    return { ok: true, items: [item] }
  })
}

afterEach(() => {
  cleanup()
  api.request.mockReset()
})

describe('CompressPrefixSwitch', () => {
  it('读数来自开关中心，默认收起的说明点开后有真实数字', async () => {
    setup()
    render(<CompressPrefixSwitch />)

    await waitFor(() => expect(api.request).toHaveBeenCalledWith('/api/cp/settings'))
    const sw = await screen.findByRole('switch')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    expect(screen.queryByText(/99\.7%/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '压缩复用说明' }))
    expect(screen.getByText(/99\.7%/)).toBeTruthy()
    expect(screen.getByText(/0 \/ 57,385/)).toBeTruthy()
    expect(screen.getByText(/1\.85 倍/)).toBeTruthy()
  })

  it('切换发 POST 到该 key（A 级、hot），成功后开关态翻转', async () => {
    setup()
    render(<CompressPrefixSwitch />)
    const sw = await screen.findByRole('switch')

    fireEvent.click(sw)
    await waitFor(() => expect(api.request).toHaveBeenCalledWith(
      '/api/cp/settings/' + KEY,
      expect.objectContaining({ method: 'POST', body: expect.objectContaining({ value: true }) }),
    ))
    await waitFor(() => expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true'))
    expect(screen.getByText(/已开启/)).toBeTruthy()
  })

  it('不可编辑时置灰并显示锁定原因', async () => {
    setup({ ...ITEM, editable: false, locked_reason: '环境变量锁定' })
    render(<CompressPrefixSwitch />)
    const sw = await screen.findByRole('switch')
    expect(sw).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '压缩复用说明' }))
    expect(screen.getByText(/环境变量锁定/)).toBeTruthy()
  })

  it('后端 ok=false 要如实上屏', async () => {
    setup()
    render(<CompressPrefixSwitch />)
    const sw = await screen.findByRole('switch')
    api.request.mockImplementation(async (_url: string, opts?: { method?: string }) => {
      if (opts?.method === 'POST') return { ok: false, message: '权限不足' }
      return { ok: true, items: [ITEM] }
    })

    fireEvent.click(sw)
    expect(await screen.findByText(/切换失败：权限不足/)).toBeTruthy()
  })
})
