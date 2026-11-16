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
 *        【2026-11-16 Owner】卡片内不再重复预览同一份内容，只保留悬停→右侧高亮。
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
import { render, screen, within, waitFor, cleanup, fireEvent, act } from '@testing-library/react'
import PromptLab from './index'

type Section = { enabled?: boolean; custom_content?: string; label?: string; token_limit?: number }
type Sections = Record<string, Section>

/** 被 mock 的配置库（测试可写；每次用例前重置） */
const h = vi.hoisted(() => ({ sections: {} as Record<string, any> }))

// 2026-11-15 人格 6 维并进因素：这些滑块会 GET /api/personality（首屏覆盖默认值）并在变化后
// 防抖 POST /api/personality/params。本文件锁的是页面结构，不锁同步机制（那条在
// personalityFactors.test.tsx 里单独锁），故这里只给一个"读得到、写得动"的桩 ——
// 顺带带上预设档，用来锁「删掉人格 Tab 之后预设没跟着消失」。
vi.mock('@/api/envelope', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    getEnvelope: vi.fn(async () => ({
      current_profile: 'custom',
      custom_params: {},
      dimensions: [],
      profiles: {
        gentle_helper: { name: '温和助人型', description: '温暖、耐心', params: {} },
        professional: { name: '专业顾问型', description: '严谨、客观', params: {} },
      },
    })),
    postEnvelope: vi.fn(async () => ({ ok: true })),
  }
})

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
    // 运行期注入的一节：不在模板里（emit_stage=runtime），模板里没有它的原文
    const lt = on(s, 'lifetrace')
    info.lifetrace = {
      emit_text: '',
      emit_order: 10000 + keys.length + 1,
      emit_stage: 'runtime',
      emitted: lt,
    }
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
        // 运行期注入的一节（不在模板里）：用来锁「模板到此为止」那段说明
        { key: 'lifetrace', label: 'LifeTrace 语义检索', description: '长期记忆检索（并入记忆线索）', tokens: 1000, emit_stage: 'runtime' },
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
  lifetrace: { enabled: true, custom_content: '', label: 'LifeTrace 语义检索', token_limit: 0 },
})

/** 右侧「系统提示词（身份提示词 · 注入）」区域（PreviewPanel.tsx:121，页面内首个 .pl-sim-out） */
const promptArea = () => document.querySelector('.pl-preview .pl-sim-out') as HTMLElement | null

/**
 * 切到「身份提示词」Tab。
 * 【2026-11-15 Owner】「把 身份提示词 及身份提示词变化产生的实时预览拆成一个 TAB」
 * ⇒ 分节配置与最终注入文本只在那个 Tab 里渲染（同一时刻只渲染一个 Tab）。
 */
const openIdentityTab = async () => {
  const tablist = await screen.findByRole('tablist', { name: '提示词实验室模块' })
  fireEvent.click(within(tablist).getByRole('tab', { name: '身份提示词' }))
  await screen.findByText('分节配置（哪些块进提示词）')
}

/** 按标题定位某个身份提示词面板项卡片 */
const cardFor = (label: string) =>
  Array.from(document.querySelectorAll('.pl-factor-card')).find((c) =>
    c.textContent?.includes(label),
  ) as HTMLElement | undefined

beforeEach(() => {
  h.sections = baseSections()
  // Tab 偏好存在 localStorage（会话级），会被上一条用例的点击留下来 —— 不清就会渲染到错的 Tab
  // （2026-11-15 实际踩到：新用例要断言影响因素页，却因为上一条点过「LLM 通信监控」而看到监控面板）。
  localStorage.removeItem('yunshu.prompt-lab.tab')
})

afterEach(cleanup)

describe('需求②：提示词实验室（顶栏 / 身份提示词联动）', () => {
  it('顶栏不再提供「返回工作台」按钮', async () => {
    render(<PromptLab />)
    await openIdentityTab()
    expect(screen.queryByText('返回工作台')).toBeNull()
  })

  it('技能指令启用 → {skill_instructions} 显示在提示词区域；停用 → 不再显示（联动）', async () => {
    render(<PromptLab />)
    await openIdentityTab()

    // 载入完成且后端模板已回填：启用态 ⇒ 提示词区域出现该节发出内容。
    // 【为什么用 vi.waitFor 而不是 RTL 的 waitFor】这一步等的是**真实 fetch 链**（GET 配置 →
    // POST 预览 → setTemplate），下一步等的是组件内 600ms 防抖（用假定时器推进）。两段各自
    // 用对的时序工具：这里让 vitest 用真实定时器轮询"DOM 出现这段文案"，不把它塞进 act；
    // 下一步由测试显式推进假定时器。没有任何一段是固定预算的 sleep。
    //   代价（明写，免得后人当新问题查）：本文件这些异步更新不再被 act 包住，会多出一条
    //   "not wrapped in act" 警告（全量跑已有 240 条同类）；换来的是这一步的等待与后面的
    //   假定时器段互不牵扯。
    await vi.waitFor(() => {
      expect(promptArea()?.textContent ?? '').toContain('{skill_instructions}')
    })
    // 【2026-11-16 Owner】卡片内**不再**重复预览发出内容：同一份文本只在右侧实时预览里出现。
    // 这里改成反向断言 —— 卡片里不能有这段文本（否则就是又长出一份会和右侧不一致的副本）。
    const card = cardFor('技能指令')
    expect(card).toBeTruthy()
    expect(card?.textContent ?? '').not.toContain('{skill_instructions}')

    // 点击该面板项的开关（停用）→ 600ms 防抖后重建模板 → 提示词区域不再包含占位符
    // 【2026-11-16】开关统一成 ui/Switch（role="switch" + aria-checked），不再是 aria-pressed 的
    // pill 按钮；开关本身就是状态，卡片里也没有「启用/停用」文字列了。
    const toggle = within(card as HTMLElement).getByRole('switch')
    expect(toggle.getAttribute('aria-checked')).toBe('true')

    // 【为什么改成**假定时器**推进】这一步等的是组件内 600ms 防抖之后的模板重建
    // （pages/prompt-lab/identityPrompt.ts 的 PREVIEW_DEBOUNCE_MS）。原先用「真实时间 +
    // 固定 4000ms 预算」，于是它实际上是**在断言机器跑得多快**：
    //   · 实测过一次 CI 红，而那次提交**只改了一个 .md**（即与被测代码无关）；
    //   · 本机同一文件连跑 13 次里也红过 1 次（首次运行、Vite 缓存还是冷的）。
    // 现在时间由测试自己推进 ⇒ 与 runner 快慢、覆盖率插桩、并行度都无关，
    // 而判据仍然钉在机制上：**防抖到点之后模板必须被重建**。
    // 推进 2000ms 是「≥ 600ms 防抖」的余量，不是新的时间预算（没有真实时钟参与）。
    vi.useFakeTimers()
    try {
      fireEvent.click(toggle)
      // 【判据前移：点击必须**当场**生效】点击 → onToggle → setSections 是同步链路，act 会把它
      // 冲刷掉，所以这里不需要任何等待：`aria-checked` 必须立刻翻成 false。放在推进定时器
      // **之前**断言，是为了把"点击被吞掉"和"防抖没重建"两种坏法分开 ——
      // 2026-10-18 的 CI flake（run 39083064797 attempt 1，红在下面的模板断言）实际是前者：
      // onToggle 读到尚未被 passive effect 同步的 sectionsRef（空对象）后静默早退，于是模板
      // 仍是全启用；失败信息却指向"模板没重建"，把人往防抖那条线上带。根因已修在
      // identityPrompt.ts（ref 改用 useLayoutEffect 同步），这条断言负责**钉住它不再回来**。
      expect(
        toggle.getAttribute('aria-checked'),
        '点击没有生效：开关状态仍为启用（onToggle 早退 / 事件被吞），后面的模板断言会因此误报',
      ).toBe('false')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
    } finally {
      vi.useRealTimers()
    }

    expect(promptArea()?.textContent ?? '').not.toContain('{skill_instructions}')
    // 其余启用节仍在（证明是「该节」被去掉，而不是整块模板塌掉）
    expect(promptArea()?.textContent ?? '').toContain('{tool_status}')
    expect(promptArea()?.textContent ?? '').toContain('{memory_context}')
  })

  it('未启用的面板项不显示发出内容（未发出）', async () => {
    h.sections = { ...baseSections(), skill_instructions: { enabled: false, custom_content: '', label: '技能指令', token_limit: 0 } }
    render(<PromptLab />)
    await openIdentityTab()
    await waitFor(() => {
      expect(promptArea()?.textContent ?? '').not.toContain('{skill_instructions}')
    })
    // 卡片就地状态：**开关本身**（role=switch + aria-checked=false）就是状态列，
    // 不再有「已停用（不参与组装）」这类文字；发出内容只在右侧提示词区域体现。
    const card = cardFor('技能指令') as HTMLElement
    expect(within(card).getByRole('switch').getAttribute('aria-checked')).toBe('false')
    expect(card.textContent ?? '').not.toContain('{skill_instructions}')
    // 只禁掉**状态句式**（描述文案里出现"已启用技能"是正常的说明，不是状态列）
    expect(card.textContent ?? '').not.toMatch(/已启用（运行时自动注入）|已停用（不参与组装）/)
  })
})

describe('拆 Tab（Owner 2026-11-15）：身份提示词 + 它的实时预览独占一屏', () => {
  it('基础模板编辑器已删除；两个 Tab 各自「一列可调 + 预览」，拖拽条都在', async () => {
    render(<PromptLab />)

    // Owner：「基础模板可以不要，分节配置的开关本身就是在组装基础模板」
    // ⇒ 那个 details.pl-base-block（模板编辑器）必须整块消失，连编辑框都不该在 DOM 里
    expect(document.querySelector('details.pl-base-block'), '基础模板折叠块已删除').toBeNull()
    expect(screen.queryByLabelText('系统提示词模板'), '模板编辑器已删除').toBeNull()

    // 影响因素 Tab：因素列 + 生成的提示词（因素滑块的产物）
    expect(screen.getByText('生成的提示词（左侧因素实时生成 · 试跑用它）')).toBeTruthy()
    // 这一份只在试跑时发（不是线上 system message）—— 文案里有 <b> 分段，故按整块 textContent 判
    const factorPreviewText = document.querySelector('.pl-preview')?.textContent ?? ''
    expect(factorPreviewText).toContain('试跑时')
    expect(factorPreviewText).toContain('不会')
    expect(factorPreviewText).toContain('真发的 system message 在「身份提示词」Tab')
    let splits = screen.getAllByRole('separator')
    expect(splits.map((s) => s.getAttribute('aria-label'))).toEqual(['拖动调整因素列宽'])
    expect(splits.every((s) => s.getAttribute('aria-orientation') === 'vertical')).toBe(true)

    // 身份提示词 Tab：分节配置 + 它组装出来的最终注入文本
    await openIdentityTab()
    expect(screen.getByText('最终注入文本（只读）')).toBeTruthy()

    // 「哪份是真发的」+「模板到此为止」两句必须在（Owner 2026-11-15 两个追问的落点）
    expect(screen.getByText(/线上每次对话真正发出的 system message/)).toBeTruthy()
    const offTpl = document.querySelector('.pl-off-template') as HTMLElement | null
    expect(offTpl, '要有「模板到此为止」说明块').toBeTruthy()
    expect(offTpl!.textContent ?? '').toContain('LifeTrace 语义检索')
    expect(offTpl!.textContent ?? '').toContain('运行期注入')
    // Owner 追问「为什么到记忆线索就没了」的正面答案：那段在 system message 里恒为空，
    // 检索内容随本轮最后一条消息下发（2026-11-15 实测：检索档 120 token 挂到最后一条 user 消息）
    expect(offTpl!.textContent ?? '').toContain('恒为空')
    expect(offTpl!.textContent ?? '').toContain('本轮最后一条消息')
    splits = screen.getAllByRole('separator')
    expect(splits.map((s) => s.getAttribute('aria-label'))).toEqual(['拖动调整身份提示词列宽'])
    expect(splits.every((s) => s.getAttribute('aria-orientation') === 'vertical')).toBe(true)
  })

  it('每个分节开关的改动都反映在最终注入文本里（Owner 的核心诉求）', async () => {
    render(<PromptLab />)
    await openIdentityTab()
    await vi.waitFor(() => {
      expect(promptArea()?.textContent ?? '').toContain('{tool_status}')
    })
    const card = cardFor('工具与技能状态')
    expect(card).toBeTruthy()
    const toggle = within(card as HTMLElement).getByRole('switch')

    vi.useFakeTimers()
    try {
      fireEvent.click(toggle)
      expect(toggle.getAttribute('aria-checked')).toBe('false')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
    } finally {
      vi.useRealTimers()
    }
    // 关掉这一节 ⇒ ② 里那一节立刻消失（其余节仍在）
    expect(promptArea()?.textContent ?? '').not.toContain('{tool_status}')
    expect(promptArea()?.textContent ?? '').toContain('{memory_context}')
  })

  it("存量 Tab 值 'system-prompt' 归一到影响因素页（老用户刷新不落到空 Tab）", async () => {
    localStorage.setItem('yunshu.prompt-lab.tab', 'system-prompt')
    render(<PromptLab />)

    const factorsTab = await screen.findByRole('tab', { name: '提示词影响因素实验室' })
    expect(factorsTab.getAttribute('aria-selected'), '系统提示词 Tab 已取消，存量值必须归一').toBe('true')
    // 归一后的值要写回去，否则每次刷新都要再归一一次
    expect(localStorage.getItem('yunshu.prompt-lab.tab')).toBe('factors')
  })
})

describe('需求③：LLM 通信监控位于主内容区并以 TAB 并列', () => {
  it('主内容区 Tab 条含「提示词影响因素实验室」与「LLM 通信监控」，切换后渲染监控', async () => {
    render(<PromptLab />)
    const tablist = await screen.findByRole('tablist', { name: '提示词实验室模块' })
    const labels = within(tablist)
      .getAllByRole('tab')
      .map((t) => (t.textContent ?? '').trim())
    // 2026-11-15 三次收口后的 Tab 条：
    //   · 系统提示词 Tab 取消（其后基础模板编辑器也整块删除）；
    //   · 人格不再两份：6 维 + 预设 + 重置全在影响因素的「人格」分类里；
    //   · 「身份提示词」独立成 Tab（分节配置 + 它组装出来的最终注入文本）。
    expect(labels).toEqual(['提示词影响因素实验室', '身份提示词', 'LLM 通信监控'])

    // 默认在影响因素实验室：监控面板未挂载（不在页面最底部）
    expect(screen.queryByText('发送 Tokens')).toBeNull()

    fireEvent.click(within(tablist).getByRole('tab', { name: /LLM 通信监控/ }))
    // 监控面板已挂载并展示统计条
    expect(await screen.findByText('发送 Tokens')).toBeTruthy()
    expect(screen.getByText('接收 Tokens')).toBeTruthy()

    // 2026-11-15：「人格」Tab 已删除（人格并进影响因素）
    expect(within(tablist).queryByRole('tab', { name: '人格' })).toBeNull()
  })
})

describe('人格不再两份（Owner 2026-11-15）：只在影响因素页，含预设与重置', () => {
  it('人格分类里有 6 个滑块、预设档与重置按钮，且不再有「人格」Tab', async () => {
    render(<PromptLab />)
    await screen.findByText('生成的提示词（左侧因素实时生成 · 试跑用它）')

    // 6 个滑块（0–1：top_p 也是 max=1，故按 aria-label 精确点）
    for (const name of ['语气', '情感', '简练', '主动', '幽默', '同理心']) {
      expect(screen.getByRole('slider', { name }), '人格滑块缺失：' + name).toBeTruthy()
    }

    // 原「人格」Tab 上的预设与重置搬到这里（能力不随 Tab 消失）
    const presets = screen.getByRole('group', { name: '人格预设' })
    expect(within(presets).getByRole('button', { name: '温和助人型' })).toBeTruthy()
    expect(within(presets).getByRole('button', { name: '专业顾问型' })).toBeTruthy()
    expect(within(presets).getByRole('button', { name: '重置' })).toBeTruthy()
  })
})
