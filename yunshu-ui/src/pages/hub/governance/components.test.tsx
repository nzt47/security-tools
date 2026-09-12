/**
 * 治理面板通用组件测试（TASK-S6-01）
 * ------------------------------------------------------------------
 * 覆盖验收项：
 *   §0.3 口径纪律：缺位渲染 "—"（**绝不渲染 0**）；样本 <20 带"仅披露"
 *   §7 UI 五坑②：虚拟滚动阈值 500
 *   §7 UI 五坑③：隐式入口可点击记录
 *   U1：TaintBadge class / 审批区 z-index **取自后端常量**（未自定义）
 *   U2：审批区 Shadow DOM 自治单元 + 固定 z-index
 *   §7 状态灯五态（灰/蓝/黄/绿/红）
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, act } from '@testing-library/react'
import {
  ApprovalZone,
  MetricValue,
  ReasonChain,
  StatusBadge,
  TaintBadge,
  VirtualList,
  VIRTUAL_SCROLL_THRESHOLD,
  toneForRisk,
  toneForSeverity,
  toneForStage,
} from './components'
import {
  ImplicitEntryLog,
  implicitEntries,
  recordImplicitEntry,
  resetImplicitEntries,
} from './implicitEntry'
import { resetSecurityStateCache } from './usePanel'
import type { Metric } from '@/lib/cpPanelsTypes'

// ── 后端常量（**测试用固定值**；生产一律从 /api/cp/security/render-state 取）──

const SECURITY_STATE = {
  ok: true,
  generated_at: '2026-09-12T00:00:00',
  safe_render: {
    allowed_tags: ['p', 'span'],
    csp: { 'script-src': "'none'" },
    iframe_sandbox: 'allow-same-origin',
    image_proxy_prefix: '/api/cp/image-proxy?url=',
    system_slots: ['system.status'],
    taint_badge: {
      class: 'cp-taint-badge',
      base_class: 'cp-taint-badge--has-bg',
      attr: 'data-cp-taint-source',
      always_has_background: true,
    },
    approval_zone: {
      class: 'cp-approval-zone',
      style: {
        position: 'fixed',
        'z-index': '2147483000',
        isolation: 'isolate',
        'pointer-events': 'auto',
      },
      shadow_root: true,
    },
  },
  boundary_words: {
    enabled: true,
    never_automated: ['transfer', 'publish', 'drop_database', 'permission_change', 'force_push'],
    labels: {
      transfer: '转账',
      publish: '发布',
      drop_database: '删库',
      permission_change: '改权限',
      force_push: 'push --force',
    },
    ttl_seconds: 60,
    max_ttl_seconds: 60,
    single_action_bound: true,
    accepts_text_approval: false,
    store: {},
  },
  injection_defense: {},
  frontend_contract: { must_not_customize: [], rule: '' },
}

vi.mock('@/lib/cpPanelsApi', () => ({
  fetchSecurityRenderState: vi.fn(() => Promise.resolve(SECURITY_STATE)),
}))

function metric(over: Partial<Metric> = {}): Metric {
  return {
    value: 57,
    available: true,
    source: 'agent/digestion/shadow.py::ShadowReport.pass_rate',
    formula: 'passed / sampled',
    unit: 'ratio',
    dataset: 'shadow_ledger.jsonl',
    note: '',
    sample_size: 57,
    min_sample: 20,
    insufficient_sample: false,
    disclosure_only: false,
    traceable: true,
    ...over,
  }
}

beforeEach(() => {
  resetSecurityStateCache()
  resetImplicitEntries()
})
afterEach(cleanup)

// ════════════════════════════════════════════════════════════

describe('§0.3 口径纪律：MetricValue', () => {
  it('缺位（value=null）渲染 "—"，**绝不渲染 0**', () => {
    render(<MetricValue metric={metric({ value: null, available: false })} />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.queryByText('0')).toBeNull()
  })

  it('缺位时 DOM 标记为 absent（供 E2E/视觉回归断言）', () => {
    const { container } = render(
      <MetricValue metric={metric({ value: null, available: false })} />,
    )
    expect(container.querySelector('[data-cp-metric-value="absent"]')).toBeTruthy()
  })

  it('样本 <20 显示"仅披露"角标', () => {
    render(<MetricValue metric={metric({ sample_size: 4, insufficient_sample: true, disclosure_only: true })} />)
    expect(screen.getByText('仅披露')).toBeInTheDocument()
  })

  it('样本达标不显示"仅披露"角标', () => {
    render(<MetricValue metric={metric()} />)
    expect(screen.queryByText('仅披露')).toBeNull()
  })

  it('"来源"入口展开后显示数据源与公式（可追溯）', () => {
    render(<MetricValue metric={metric()} />)
    fireEvent.click(screen.getByLabelText('来源与口径'))
    expect(screen.getByText(/agent\/digestion\/shadow\.py::ShadowReport\.pass_rate/)).toBeInTheDocument()
    expect(screen.getByText(/passed \/ sampled/)).toBeInTheDocument()
  })

  it('比率按百分比渲染', () => {
    render(<MetricValue metric={metric({ value: 0.9649 })} format="percent" />)
    expect(screen.getByText('96.5%')).toBeInTheDocument()
  })
})

describe('§7 状态灯五态', () => {
  it('五态各自渲染对应 tone 标记（灰/蓝/黄/绿/红）', () => {
    const tones = ['gray', 'blue', 'yellow', 'green', 'red'] as const
    for (const tone of tones) {
      const { container, unmount } = render(<StatusBadge tone={tone}>x</StatusBadge>)
      expect(container.querySelector(`[data-cp-status-tone="${tone}"]`)).toBeTruthy()
      unmount()
    }
  })

  it('stage / risk / severity 映射到五态（只做展示映射）', () => {
    expect(toneForStage('borrowed')).toBe('gray')
    expect(toneForStage('mirrored')).toBe('blue')
    expect(toneForStage('shadow')).toBe('yellow')
    expect(toneForStage('internalized')).toBe('green')
    expect(toneForStage('deprecated')).toBe('red')
    expect(toneForRisk('destructive')).toBe('red')
    expect(toneForRisk(null)).toBe('gray')
    expect(toneForSeverity('L5')).toBe('red')
    expect(toneForSeverity('L2')).toBe('blue')
  })
})

describe('§7 五坑②：虚拟滚动阈值 500', () => {
  it('500 条及以下不虚拟化（直接渲染）', () => {
    const items = Array.from({ length: VIRTUAL_SCROLL_THRESHOLD }, (_, i) => i)
    const { container } = render(
      <VirtualList
        items={items}
        itemHeight={20}
        height={200}
        keyOf={(n) => String(n)}
        renderItem={(n) => <span>{n}</span>}
      />,
    )
    expect(container.querySelector('[data-cp-virtualized="false"]')).toBeTruthy()
    expect(container.textContent).toContain('499')
  })

  it('超过 500 条启用虚拟化，且只渲染可视窗口', () => {
    const items = Array.from({ length: VIRTUAL_SCROLL_THRESHOLD + 200 }, (_, i) => i)
    const { container } = render(
      <VirtualList
        items={items}
        itemHeight={20}
        height={200}
        keyOf={(n) => String(n)}
        renderItem={(n) => <span data-cp-row>{n}</span>}
      />,
    )
    const host = container.querySelector('[data-cp-virtualized="true"]')
    expect(host).toBeTruthy()
    expect(host?.getAttribute('data-cp-total')).toBe(String(items.length))
    const rendered = container.querySelectorAll('[data-cp-row]').length
    // 可视 10 行 + overscan 4×2 = 18（远小于 700）
    expect(rendered).toBeLessThan(40)
  })

  it('滚动后窗口跟随（首行随之变化）', () => {
    const items = Array.from({ length: 1000 }, (_, i) => i)
    const { container } = render(
      <VirtualList
        items={items}
        itemHeight={20}
        height={200}
        keyOf={(n) => String(n)}
        renderItem={(n) => <span data-cp-row>{n}</span>}
      />,
    )
    const host = container.querySelector('[data-cp-virtualized="true"]') as HTMLElement
    expect(container.textContent).toContain('0')
    act(() => {
      host.scrollTop = 4000
      fireEvent.scroll(host)
    })
    expect(container.textContent).toContain('200')
  })
})

describe('U1：TaintBadge 常量驱动', () => {
  it('class 名取自后端常量（cp-taint-badge / --has-bg）', async () => {
    render(<TaintBadge source="mcp.result" />)
    const el = await screen.findByTitle(/外来内容/)
    expect(el.className).toContain('cp-taint-badge')
    expect(el.className).toContain('cp-taint-badge--has-bg')
    expect(el.getAttribute('data-cp-taint-source')).toBe('mcp.result')
  })

  it('常量未就绪（加载中）时**不渲染**徽章（宁可没有，也不自定义）', () => {
    const { container } = render(<TaintBadge source="x" />)
    // 首次渲染常量尚未到位 ⇒ 无徽章（随后 effect 拉到常量才出现）
    expect(container.querySelector('.cp-taint-badge')).toBeNull()
  })
})

describe('U1/U2：审批区 Shadow DOM 自治单元', () => {
  it('使用后端给的 z-index / isolation，且挂在 Shadow DOM 上', async () => {
    const { container } = render(
      <ApprovalZone inline>
        <button type="button">批准</button>
      </ApprovalZone>,
    )
    // 常量到位后出现审批区
    const host = await vi.waitFor(() => {
      const el = container.querySelector('[data-cp-approval-zone="true"]')
      expect(el).toBeTruthy()
      return el as HTMLElement
    })
    expect(host.className).toContain('cp-approval-zone')
    expect(host.getAttribute('data-cp-z-index')).toBe('2147483000')
    expect(host.getAttribute('data-cp-shadow-root')).toBe('true')
    expect(host.style.zIndex).toBe('2147483000')
    expect(host.style.isolation).toBe('isolate')
    expect(host.shadowRoot).toBeTruthy()      // ★ Shadow DOM 保留
  })

  it('按钮渲染进影子树（宿主 DOM 查询不到）', async () => {
    const { container } = render(
      <ApprovalZone inline>
        <button type="button">批准</button>
      </ApprovalZone>,
    )
    await vi.waitFor(() => {
      expect(container.querySelector('[data-cp-approval-zone="true"]')).toBeTruthy()
    })
    await act(async () => { await Promise.resolve() })
    const host = container.querySelector('[data-cp-approval-zone="true"]') as HTMLElement
    const inShadow = host.shadowRoot?.querySelector('button')
    // 审批按钮只存在于影子树内（DOM 隔离：宿主样式/脚本不进入）
    expect(inShadow).toBeTruthy()
    expect(container.querySelector('button')).toBeNull()
  })
})

describe('§7 五坑③：隐式入口可点击记录', () => {
  it('登记后出现在记录里，且可点击回入口', () => {
    const onReplay = vi.fn()
    const { rerender } = render(<ImplicitEntryLog onReplay={onReplay} />)
    act(() => {
      recordImplicitEntry({
        entry: 'context-menu:distill-as-skill',
        entryLabel: '右键 → 沉淀为 Skill',
        target: 'cp.fs.write',
        outcome: 'ok',
      })
    })
    rerender(<ImplicitEntryLog onReplay={onReplay} />)
    const btn = screen.getByText('右键 → 沉淀为 Skill').closest('button') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.getAttribute('data-cp-implicit-entry')).toBe('context-menu:distill-as-skill')
    fireEvent.click(btn)
    expect(onReplay).toHaveBeenCalledTimes(1)
    expect(implicitEntries()[0].target).toBe('cp.fs.write')
  })

  it('空态给出说明而不是空白', () => {
    render(<ImplicitEntryLog />)
    expect(screen.getByText(/暂无隐式入口记录/)).toBeInTheDocument()
  })
})

describe('P7.2-24 可解释性：ReasonChain', () => {
  it('展开后逐步显示判定/实测/阈值/证据来源（非原始 JSON）', () => {
    render(
      <ReasonChain
        title="六条件"
        nodes={[
          { label: 'roi_positive', passed: true, value: '月省 ¥340', source: 's2-03_utc' },
          { label: 'p99', passed: false, value: '420ms', source: 'shadow_gray' },
        ]}
      />,
    )
    expect(screen.queryByText('roi_positive')).toBeNull()
    fireEvent.click(screen.getByText('六条件'))
    expect(screen.getByText('roi_positive')).toBeInTheDocument()
    expect(screen.getByText(/s2-03_utc/)).toBeInTheDocument()
    expect(screen.getAllByText(/证据：/).length).toBe(2)
    expect(screen.getAllByText(/shadow_gray/).length).toBeGreaterThan(0)
  })
})
