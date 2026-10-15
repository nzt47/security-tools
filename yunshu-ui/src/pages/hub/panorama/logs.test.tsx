/**
 * 日志查看页守卫（logs.tsx）
 * ------------------------------------------------------------------
 * 锁死 2026-10-15 修复：
 *   1. level/logger 从 labels 取（顶层有则优先）——真实数据在 labels 里；
 *   2. timestamp 是 Unix 秒 ⇒ 格式化成本地时间，而不是原样显示数字；
 *   3. 空列表给出可诊断提示（后端回退实际扫描的窗口/文件），不是空白表。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

function json(body: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: () => null }, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response
}

function fmtLocal(ts: number) {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const TS_INFO = 1791894668.12
const TS_ERROR = 1791895758.33
const TS_TOP = 1791896000.5

const LOGS_RESPONSE = {
  logs: [
    { timestamp: TS_INFO, labels: { app: 'yunshu-route', level: 'INFO', logger: 'agent.orchestrator' }, message: '语义层命中' },
    { timestamp: TS_ERROR, labels: { app: 'agent-config', level: 'ERROR', logger: 'agent.config' }, message: '配置写入失败' },
    // 顶层 level 优先于 labels.level
    { timestamp: TS_TOP, level: 'WARN', labels: { level: 'INFO', logger: 'agent.top' }, message: '顶层优先' },
  ],
  total: 3,
  limit: 100,
  timestamp: 1,
  window: { source: 'local', window_start: '2026-10-08T12:00:00', window_end: '2026-10-15T12:00:00', window_days: 7, files_scanned: ['2026-10-13.jsonl'], matched: 3 },
}

const EMPTY_RESPONSE = {
  logs: [],
  total: 0,
  limit: 100,
  timestamp: 1,
  window: { source: 'local', window_start: '2026-10-08T12:00:00', window_end: '2026-10-15T12:00:00', window_days: 7, files_scanned: ['2026-10-13.jsonl'], matched: 0, filters: { level: 'INFO', text_terms: [] } },
}

let fetchMock = vi.fn(async () => json(LOGS_RESPONSE))
vi.stubGlobal('fetch', (...args: unknown[]) => (fetchMock as unknown as (...a: unknown[]) => unknown)(...args))

afterEach(() => {
  cleanup()
  fetchMock = vi.fn(async () => json(LOGS_RESPONSE))
})

const LogsPage = (await import('./logs')).default

describe('日志查看 · labels 与时间戳', () => {
  it('level/logger 取自 labels；Unix 秒格式化成本地时间', async () => {
    render(<LogsPage />)

    expect(await screen.findByText('语义层命中')).toBeTruthy()
    // 级别/logger 必须取自 labels（页面里 <select> 也有同名 option，故按行取）
    const rows = screen.getAllByTestId(/^log-row-/)
    expect(rows[0].textContent).toContain('INFO')
    expect(rows[0].textContent).toContain('agent.orchestrator')
    expect(rows[1].textContent).toContain('ERROR')
    expect(rows[1].textContent).toContain('agent.config')
    // timestamp 是 Unix 秒 ⇒ 必须格式化
    expect(screen.getByText(fmtLocal(TS_INFO))).toBeTruthy()
    expect(screen.getByText(fmtLocal(TS_ERROR))).toBeTruthy()
    expect(screen.queryByText(String(TS_INFO))).toBeNull()
  })

  it('顶层 level 优先于 labels.level', async () => {
    render(<LogsPage />)
    expect(await screen.findByText('顶层优先')).toBeTruthy()
    const rows = screen.getAllByTestId(/^log-row-/)
    expect(rows[2].textContent).toContain('WARN')
    expect(rows[2].textContent).not.toContain('INFO')
  })
})

describe('日志查看 · 空结果可诊断', () => {
  it('空列表显示"已扫描的窗口"与文件名，而不是空白', async () => {
    fetchMock = vi.fn(async () => json(EMPTY_RESPONSE))
    render(<LogsPage />)

    const empty = await screen.findByTestId('logs-empty')
    expect(empty.textContent).toContain('没有匹配的日志')
    expect(empty.textContent).toContain('已扫描的窗口')
    expect(empty.textContent).toContain('2026-10-13.jsonl')
  })
})

describe('日志查看 · 失败上屏', () => {
  it('接口 500 显示错误框', async () => {
    fetchMock = vi.fn(async () => json({ error: 'boom' }, 500))
    render(<LogsPage />)
    await waitFor(() => expect(screen.getByText(/HTTP 500/)).toBeTruthy())
  })
})
