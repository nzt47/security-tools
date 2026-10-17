/**
 * 反馈中心 + 业务看板 —— 17 条端点
 * ------------------------------------------------------------------
 * 锁：反馈这一族的 {data, success} 信封（不是 {ok,...}）、列表过滤参数、
 * 标记已解决、提交需 trace_id；业务侧定义表与指标详情查询、仪表盘四面。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import FeedbackCenter from './feedback'
import BusinessBoard from './business'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function fbGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/feedback/summary')) {
      return { success: true, data: { total_feedback: 7, like_count: 5, dislike_count: 2, satisfaction_rate_percent: 71, quality_cases_count: 3 } }
    }
    if (url.startsWith('/api/feedback/list')) {
      return { success: true, data: { items: [{ feedback_id: 'f1', feedback_type: 'dislike', status: 'open', comment: '答得不对' }], total: 1 } }
    }
    if (url.startsWith('/api/feedback/report')) return { success: true, data: { negative_feedback_count: 2, optimization_suggestions: ['补上下文'] } }
    if (url.startsWith('/api/feedback/quality-cases')) return { success: true, data: [{ case_id: 'c1', title: '优质案例', content_summary: '很棒' }] }
    if (url.startsWith('/api/feedback/f1')) return { success: true, data: { feedback_id: 'f1', comment: '答得不对' } }
    return { success: true, data: {} }
  })
}

describe('FeedbackCenter', () => {
  it('读 {data,success} 信封：汇总与列表上屏', async () => {
    fbGets()
    render(<FeedbackCenter />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/feedback/summary?days=7'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/feedback/list?limit=50&offset=0'))
    expect(await screen.findByText('71%')).toBeTruthy()
    expect(screen.getByText('答得不对')).toBeTruthy()
  })

  it('按状态/类型过滤时把参数带上', async () => {
    fbGets()
    render(<FeedbackCenter />)
    await screen.findByText('答得不对')

    fireEvent.change(screen.getByLabelText('反馈状态'), { target: { value: 'open' } })
    fireEvent.change(screen.getByLabelText('反馈类型'), { target: { value: 'dislike' } })
    fireEvent.click(screen.getByRole('button', { name: '按条件查询反馈' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith(
      '/api/feedback/list?limit=50&offset=0&status=open&feedback_type=dislike',
    ))
  })

  it('标记已解决走 POST resolve；详情走 GET <id>', async () => {
    fbGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<FeedbackCenter />)
    await screen.findByText('答得不对')

    fireEvent.click(screen.getByRole('button', { name: '查看反馈 f1' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/feedback/f1'))

    fireEvent.click(screen.getByRole('button', { name: '解决反馈 f1' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/feedback/f1/resolve', {
      resolution: '已在反馈中心处理',
    }))
  })

  it('提交反馈：没有 trace_id 时禁用', async () => {
    fbGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<FeedbackCenter />)
    await screen.findByText('答得不对')

    fireEvent.click(screen.getByRole('button', { name: '提交反馈' })) // 切到「提交反馈」Tab
    const submit = screen.getByRole('button', { name: '提交新反馈' }) as HTMLButtonElement
    expect(submit.disabled, '后端对空 trace_id 直接 400').toBe(true)
    fireEvent.change(screen.getByLabelText('反馈 trace_id'), { target: { value: 't-9' } })
    expect(submit.disabled).toBe(false)
    fireEvent.click(submit)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/feedback/submit', {
      trace_id: 't-9', feedback_type: 'suggestion', rating: 5, comment: '',
    }))
  })
})

function bizGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/business/dashboard') return { interaction: { yunshu_interaction_total: { data: 12, unit: '次' } } }
    if (url === '/api/business/definitions') {
      return { categories: { interaction: 1 }, definitions: [{ name: 'yunshu_interaction_total', category: 'interaction', metric_type: 'counter', unit: '次', business_value: '衡量活跃度' }] }
    }
    if (url === '/api/business/health') return { health_score: 0, status: 'critical' }
    if (url.startsWith('/api/business/metrics/')) return { name: 'yunshu_interaction_total', value: 12 }
    if (url === '/api/dashboard/health') return { status: 'healthy', services: { memory: true } }
    if (url === '/api/dashboard/quality') return { critic_scores: { average_score: 77.23, total_evaluations: 662 } }
    if (url === '/api/dashboard/memory') return { hit_rate: { overall_hit_rate: 73.5 } }
    if (url.startsWith('/api/dashboard/traces')) return { traces: [{ trace_id: 'tr1', name: 'chat', duration_ms: 30 }] }
    return {}
  })
}

describe('BusinessBoard', () => {
  it('业务侧：定义表与健康分上屏；点指标名可查详情', async () => {
    bizGets()
    render(<BusinessBoard />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/business/definitions'))
    expect(await screen.findByText('yunshu_interaction_total')).toBeTruthy()
    expect(screen.getByText('critical')).toBeTruthy()

    fireEvent.click(screen.getByText('yunshu_interaction_total'))
    fireEvent.click(screen.getByRole('button', { name: '查询指标详情' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/business/metrics/yunshu_interaction_total'))
  })

  it('系统仪表盘：四面并发 + Prometheus 是外链而非 JSON 请求', async () => {
    bizGets()
    render(<BusinessBoard />)
    fireEvent.click(screen.getByRole('button', { name: '系统仪表盘' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/dashboard/health'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/dashboard/quality'))
    expect(await screen.findByText('healthy')).toBeTruthy()
    // 77.23 会同时出现在指标卡与质量面 JSON 里 —— 按"至少一处"断言
    await waitFor(() => expect(screen.getAllByText(/77.23/).length).toBeGreaterThan(0))

    const link = screen.getByRole('link', { name: '导出 Prometheus 指标' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/business/prometheus')
    expect(api.hubGet).not.toHaveBeenCalledWith('/api/business/prometheus')
  })
})
