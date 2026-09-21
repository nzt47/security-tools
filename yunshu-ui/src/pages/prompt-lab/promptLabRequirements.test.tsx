/**
 * promptLab 需求回归测试 —— 工作台三项需求的「页面级」锁定
 * ------------------------------------------------------------------
 * 既有 promptLab.test.tsx 只覆盖叶子控件（FactorControl / FactorCard /
 * RadarChart），**没有任何用例锁定需求①/②/③在页面级的行为**，故新增本文件：
 *
 *   ② 提示词实验室
 *      - 顶栏不再提供「返回工作台」按钮；
 *      - 「身份提示词（系统提示词 · 线上配置）」面板项的**启用状态 ↔ 提示词
 *        区域内容显示联动**：启用 → 右侧「系统提示词（身份提示词 · 注入）」
 *        出现该节的发出内容（技能指令 → {skill_instructions}）；停用 → 不再出现。
 *
 *   ③ LLM 通信监控
 *      - 位于**主内容区**，与「提示词影响因素实验室」以 **TAB 并列**展示
 *        （而非原页面最底部）。
 *
 * 被测数据流（与线上同一条链路，见 identityPrompt.ts）：
 *   GET  /api/system-prompt/config          → sections / registry / emit_info
 *   POST /api/system-prompt/config/preview  → 后端模板引擎产出的真实注入模板
 *   右侧提示词区域 = identity.template（index.tsx:104 → PreviewPanel.systemPrompt）
 *
 * 为了让「联动」被真正验证（而不是透传一个固定字符串），preview 的桩实现成
 * **与后端同序的渲染函数**（mirror agent/system_prompt_config.py 的
 * SECTION_REGISTRY 渲染顺序）：模板内容只由面板项的 enabled 决定。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, waitFor, cleanup, fireEvent } from '@testing-library/react'
import PromptLab from './index'

type Section = { enabled?: boolean; custom_content?: string; label?: string; token_limit?: number }
type Sections = Record<string, Section>

/** 被 mock 的配置库（测试可写；每次用例前重置） */
const h = vi.hoisted(() => ({ sections: {} as Record<string, any> }))

vi.mock('../hub/components/ui', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()

  const on = (s: Sections, k: string) => s[k]?.enabled !== false

  /** 与后端 SECTION_REGISTRY 同序的极简模板渲染（system_prompt_config.py:353-487） */
  const renderTemplate = (s: Sections): string => {
    const parts: string[] = []
    if (on(s, 'identity')) {
      parts.push(s.identity?.custom_content?.trim() || '你是「云枢」，一个生活在电脑里的数字生命体。')
    }
    if (on(s, 'principles')) parts.push('## 核心原则\n1. 用第一人称表达感受和需求')
    if (on(s, 'skill_instructions')) parts.push('{skill_instructions}')
    if (on(s, 'tool_status')) parts.push('## 当前工具与技能状态\n以下是当前已启用/禁用的工具和技能：\n{tool_status}')
    if (on(s, 'memory_context')) parts.push('## 记忆线索\n{memory_context}')
    return parts.join('\n\n')
  }

  /** 与后端 compute_emit_info 同口径的发出信息（emit_text / emit_order / emitted） */
  const emitInfo = (s: Sections, tpl: string): Record<string, unknown> => {
    const keys = ['identity', 'principles', 'skill_instructions', 'tool_status', 'memory_context']
    const info: Record<string, unknown> = {}
    // 各节单独渲染后的原文（供前端「启用时显示发出内容」；与后端 render 函数同口径）
    const singleOf = (sec: Sections): Record<string, string> => ({
      identity: sec.identity?.custom_content?.trim() || '你是「云枢」，一个生活在电脑里的数字生命体。',
      principles: '## 核心原则\n1. 用第一人称表达感受和需求',
      skill_instructions: '{skill_instructions}',
      tool_status: '## 当前工具与技能状态\n以下是当前已启用/禁用的工具和技能：\n{tool_status}',
      memory_context: '## 记忆线索\n{memory_context}',
    })
    const single = singleOf(s)
    keys.forEach((k, idx) => {
      const enabled = on(s, k)
      info[k] = {
        emit_text: single[k],
        emit_order: enabled ? tpl.indexOf(single[k]) : 10000 + idx,
        emit_stage: 'system_prompt',
        emitted: enabled && tpl.includes(single[k]),
      }
    })
    return info
  }

  const configResponse = () => {
    const sections: Sections = h.sections
    const tpl = renderTemplate(sections)
    return {
      version: 2,
      sections,
      registry: [
        { key: 'identity', label: '基础身份设定', description: '定义 LLM 的角色身份认知。', editable: true, tokens: 350 },
        { key: 'principles', label: '核心原则', description: '行为铁律。', editable: true, tokens: 650 },
        { key: 'skill_instructions', label: '技能指令', description: '已启用技能的提示词片段。', tokens: 500 },
        { key: 'tool_status', label: '工具与技能状态', description: '文本形式列出已启用/禁用的工具和技能。', tokens: 350 },
        { key: 'memory_context', label: '记忆上下文', description: '对话历史摘要 + 最近消息。', tokens: 5000 },
      ],
      stats: {},
      emit_info: emitInfo(sections, tpl),
      emit_stage_labels: { system_prompt: 'system message 注入', tools: 'tools 参数', user_message: '用户消息前置', runtime: '运行期注入' },
      summary: { total_enabled_tokens: 1200, total_disabled_count: 0, grand_total: 4200, base_template_tokens: 150 },
      has_custom_template: false,
    }
  }

  return {
    ...actual,
    hubGet: vi.fn(async (url: string) => {
      if (url === '/api/system-prompt/config') return configResponse()
      if (url.startsWith('/api/llm-monitor/stats')) {
        return { enabled: true, total: 0, total_tokens: 0, total_request_tokens: 0, total_response_tokens: 0 }
      }
      if (url.startsWith('/api/llm-monitor/records')) return { records: [], total: 0 }
      return {}
    }),
    hubPost: vi.fn(async (url: string, body?: any) => {
      if (url === '/api/system-prompt/config/preview') {
        // 后端行为：按**当前（可未保存）**配置重新组装模板
        return { template: renderTemplate(body?.config?.sections ?? {}) }
      }
      return { ok: true }
    }),
  }
})

const baseSections = (): Sections => ({
  identity: { enabled: true, custom_content: '', label: '身份设定', token_limit: 0 },
  principles: { enabled: true, custom_content: '', label: '核心原则', token_limit: 0 },
  skill_instructions: { enabled: true, custom_content: '', label: '技能指令', token_limit: 0 },
  tool_status: { enabled: true, custom_content: '', label: '工具与技能状态', token_limit: 0 },
  memory_context: { enabled: true, custom_content: '', label: '记忆上下文', token_limit: 8192 },
})

/** 右侧「系统提示词（身份提示词 · 注入）」区域（PreviewPanel.tsx:121，页面内首个 .pl-sim-out） */
const promptArea = () => document.querySelector('.pl-preview .pl-sim-out') as HTMLElement | null

/** 按标题定位某个身份提示词面板项卡片 */
const cardFor = (label: string) =>
  Array.from(document.querySelectorAll('.pl-factor-card')).find((c) =>
    c.textContent?.includes(label),
  ) as HTMLElement | undefined

beforeEach(() => {
  h.sections = baseSections()
})

afterEach(cleanup)

describe('需求②：提示词实验室（顶栏 / 身份提示词联动）', () => {
  it('顶栏不再提供「返回工作台」按钮', async () => {
    render(<PromptLab />)
    await screen.findByText('身份提示词（系统提示词 · 线上配置）')
    expect(screen.queryByText('返回工作台')).toBeNull()
  })

  it('技能指令启用 → {skill_instructions} 显示在提示词区域；停用 → 不再显示（联动）', async () => {
    render(<PromptLab />)

    // 载入完成且后端模板已回填：启用态 ⇒ 提示词区域出现该节发出内容
    await waitFor(() => {
      expect(promptArea()?.textContent ?? '').toContain('{skill_instructions}')
    })
    // 面板项卡片内也同步显示「发出内容」（IdentityPromptPanel.tsx:209）
    const card = cardFor('技能指令')
    expect(card).toBeTruthy()
    expect(within(card as HTMLElement).getByText('{skill_instructions}')).toBeTruthy()

    // 点击该面板项的开关（停用）→ 600ms 防抖后重建模板 → 提示词区域不再包含占位符
    const toggle = within(card as HTMLElement).getByRole('button')
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(toggle)

    await waitFor(
      () => {
        expect(promptArea()?.textContent ?? '').not.toContain('{skill_instructions}')
      },
      { timeout: 4000 },
    )
    // 其余启用节仍在（证明是「该节」被去掉，而不是整块模板塌掉）
    expect(promptArea()?.textContent ?? '').toContain('{tool_status}')
    expect(promptArea()?.textContent ?? '').toContain('{memory_context}')
  })

  it('未启用的面板项不显示发出内容（未发出）', async () => {
    h.sections = { ...baseSections(), skill_instructions: { enabled: false, custom_content: '', label: '技能指令', token_limit: 0 } }
    render(<PromptLab />)
    await screen.findByText('身份提示词（系统提示词 · 线上配置）')
    await waitFor(() => {
      expect(promptArea()?.textContent ?? '').not.toContain('{skill_instructions}')
    })
    // 卡片就地状态：已停用（不参与组装），且不显示该节的发出内容
    // （IdentityPromptPanel.tsx:196-206：未启用时 emitText 置空 ⇒ 卡片与提示词区域都不显示）
    const card = cardFor('技能指令') as HTMLElement
    expect(within(card).getByText('已停用（不参与组装）')).toBeTruthy()
    expect(card.textContent ?? '').not.toContain('{skill_instructions}')
  })
})

describe('需求③：LLM 通信监控位于主内容区并以 TAB 并列', () => {
  it('主内容区 Tab 条含「提示词影响因素实验室」与「LLM 通信监控」，切换后渲染监控', async () => {
    render(<PromptLab />)
    const tablist = await screen.findByRole('tablist', { name: '提示词实验室模块' })
    const labels = within(tablist)
      .getAllByRole('tab')
      .map((t) => (t.textContent ?? '').trim())
    expect(labels).toEqual(['提示词影响因素实验室', 'LLM 通信监控'])

    // 默认在影响因素实验室：监控面板未挂载（不在页面最底部）
    expect(screen.queryByText('发送 Tokens')).toBeNull()

    fireEvent.click(within(tablist).getByRole('tab', { name: /LLM 通信监控/ }))
    // 监控面板已挂载并展示统计条
    expect(await screen.findByText('发送 Tokens')).toBeTruthy()
    expect(screen.getByText('接收 Tokens')).toBeTruthy()
  })
})
