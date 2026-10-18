/** 新增知识文档 —— content 必填；提交带 {content, source, tags[]} */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubPost: api.hubPost,
}))

import KnowledgeAddPanel from './KnowledgeAddPanel'

afterEach(() => { cleanup(); api.hubPost.mockReset() })

describe('KnowledgeAddPanel', () => {
  it('内容为空时本地拦住', async () => {
    render(<KnowledgeAddPanel />)
    fireEvent.click(screen.getByRole('button', { name: '提交知识文档' }))
    expect(await screen.findByText('内容不能为空')).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })

  it('提交带 content/source/tags 数组', async () => {
    api.hubPost.mockResolvedValue({ ok: true })
    render(<KnowledgeAddPanel />)
    fireEvent.change(screen.getByLabelText('知识文档内容'), { target: { value: '回放清理的保留期是 24h' } })
    fireEvent.change(screen.getByLabelText('知识标签'), { target: { value: '回放, 运维' } })
    fireEvent.click(screen.getByRole('button', { name: '提交知识文档' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/knowledge/add', {
      content: '回放清理的保留期是 24h', source: 'manual', tags: ['回放', '运维'],
    }))
    expect(await screen.findByText(/向量索引异步写入/)).toBeTruthy()
  })
})
