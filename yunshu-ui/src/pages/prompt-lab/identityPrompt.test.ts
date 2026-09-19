/**
 * identityPrompt —— 面板排序与「启用状态 ↔ 内容显示」联动测试
 * ------------------------------------------------------------------
 * 两条需求的不变量：
 *  1. 排序规则 = 按配置项**被拼进 system message 的先后顺序**（发出顺序）排列，
 *     未发出的项排在同组末尾；
 *  2. 面板项**启用**时给出该节的发出内容（例：技能指令启用 → {skill_instructions}），
 *     停用则不给（该节不参与组装）。
 * 排序依据取自后端「真实模板」（buildRows 的 template 参数），因此开关状态
 * 与面板顺序实时联动，不依赖再次保存配置。
 */
import { describe, expect, it } from 'vitest'
import { buildDisplayOrder, buildRows, type IdentityRawSection } from './identityPrompt'

const sections: Record<string, IdentityRawSection> = {
  identity: { key: 'identity', enabled: true, label: '身份设定', custom_content: '你是云枢。' },
  principles: { key: 'principles', enabled: true, label: '核心原则', custom_content: '## 核心原则\n先调工具。' },
  skill_instructions: { key: 'skill_instructions', enabled: true, label: '技能指令' },
  memory_context: { key: 'memory_context', enabled: true, label: '记忆上下文' },
  body_status: { key: 'body_status', enabled: false, label: '身体状态' },
  mode_info: { key: 'mode_info', enabled: false, label: '行为模式' },
  tool_status: { key: 'tool_status', enabled: false, label: '工具与技能状态列表' },
  working_memory: { key: 'working_memory', enabled: true, label: '工作记忆' },
}

const registry = [
  { key: 'identity', label: '基础身份设定', editable: true },
  { key: 'principles', label: '核心原则', editable: true },
  { key: 'skill_instructions', label: '技能指令' },
  { key: 'tool_status', label: '工具与技能状态' },
  {
    key: 'current_status',
    label: '感知层注入',
    sub_keys: ['body_status', 'mode_info'],
    children: [
      { key: 'body_status', label: '身体状态' },
      { key: 'mode_info', label: '行为模式' },
    ],
  },
  { key: 'memory_context', label: '记忆上下文' },
]

const emitInfo = {
  identity: { emit_text: '你是云枢。', emit_order: 0, emit_stage: 'system_prompt', emitted: true },
  principles: { emit_text: '## 核心原则\n先调工具。', emit_order: 8, emit_stage: 'system_prompt', emitted: true },
  skill_instructions: { emit_text: '{skill_instructions}', emit_order: 40, emit_stage: 'system_prompt', emitted: true },
  memory_context: { emit_text: '## 记忆线索\n{memory_context}', emit_order: 62, emit_stage: 'system_prompt', emitted: true },
  body_status: { emit_text: '{body_status}', emit_order: 10000.5, emit_stage: 'system_prompt', emitted: false },
  mode_info: { emit_text: '当前处于「{mode_name}」——{mode_description}', emit_order: 10000.5, emit_stage: 'system_prompt', emitted: false },
  tool_status: { emit_text: '## 当前工具与技能状态\n{tool_status}', emit_order: 10003, emit_stage: 'system_prompt', emitted: false },
  working_memory: { emit_text: '运行期注入当前任务状态', emit_order: 1006, emit_stage: 'runtime', emitted: true },
}

/** 与真实后端组装一致的模板（仅启用节按发出顺序拼装） */
const TEMPLATE = [
  '你是云枢。',
  '## 核心原则\n先调工具。',
  '{skill_instructions}',
  '## 记忆线索\n{memory_context}',
].join('\n\n')

const stageLabels = { system_prompt: 'system message 注入', runtime: '运行期注入' }

describe('buildRows · 发出顺序排序', () => {
  it('启用节按模板中的出现位置排序（发出顺序 = 面板顺序）', () => {
    const rows = buildRows(sections, registry as never, {}, emitInfo, stageLabels, TEMPLATE)
    // system message 注入节：顺序 = 在模板中的出现位置
    const injected = rows
      .filter((r) => r.emitted && r.emitStageLabel === 'system message 注入')
      .map((r) => r.key)
    expect(injected).toEqual(['identity', 'principles', 'skill_instructions', 'memory_context'])
    // 顺序与模板中的字符位置一致（严格递增）
    const orders = rows.filter((r) => r.emitted).map((r) => r.emitOrder)
    expect(orders).toEqual([...orders].sort((a, b) => a - b))
    // 运行期注入节（working_memory）排在 system message 注入节之后
    const wmIdx = rows.findIndex((r) => r.key === 'working_memory')
    const memIdx = rows.findIndex((r) => r.key === 'memory_context')
    expect(wmIdx).toBeGreaterThan(memIdx)
  })

  it('未发出的节排在同组末尾（启用与否不影响已发出节的相对顺序）', () => {
    const rows = buildRows(sections, registry as never, {}, emitInfo, stageLabels, TEMPLATE)
    const keys = rows.map((r) => r.key)
    const lastEmittedIdx = Math.max(...rows.map((r, i) => (r.emitted ? i : -1)))
    const notEmitted = rows.filter((r) => !r.emitted)
    expect(notEmitted.length).toBeGreaterThan(0)
    for (const r of notEmitted) {
      expect(keys.indexOf(r.key)).toBeGreaterThan(lastEmittedIdx)
    }
  })

  it('开关状态与模板联动：某节停用后不再出现在发出序列中', () => {
    const disabled = { ...sections, skill_instructions: { ...sections.skill_instructions, enabled: false } }
    const template = ['你是云枢。', '## 核心原则\n先调工具。', '## 记忆线索\n{memory_context}'].join('\n\n')
    const rows = buildRows(disabled, registry as never, {}, emitInfo, stageLabels, template)
    const skill = rows.find((r) => r.key === 'skill_instructions')
    expect(skill?.enabled).toBe(false)
    expect(skill?.emitted).toBe(false)
    expect(rows.filter((r) => r.emitted).map((r) => r.key)).not.toContain('skill_instructions')
  })

  it('无模板可用时回退到后端 emit_order（仍按发出顺序）', () => {
    const rows = buildRows(sections, registry as never, {}, emitInfo, stageLabels)
    expect(rows.map((r) => r.key).slice(0, 4)).toEqual([
      'identity',
      'principles',
      'skill_instructions',
      'memory_context',
    ])
  })
})

describe('buildRows · 启用状态 ↔ 内容显示联动', () => {
  it('启用节给出「发出内容」（技能指令 → {skill_instructions}）', () => {
    const rows = buildRows(sections, registry as never, {}, emitInfo, stageLabels, TEMPLATE)
    const skill = rows.find((r) => r.key === 'skill_instructions')
    expect(skill?.emitText).toBe('{skill_instructions}')
    expect(skill?.emitStageLabel).toBe('system message 注入')
  })

  it('停用节不显示发出内容（不参与组装）', () => {
    const rows = buildRows(sections, registry as never, {}, emitInfo, stageLabels, TEMPLATE)
    const toolStatus = rows.find((r) => r.key === 'tool_status')
    expect(toolStatus?.enabled).toBe(false)
    expect(toolStatus?.emitText).toBe('')
  })

  it('可编辑节有自定义内容时，发出内容以自定义内容为准', () => {
    const rows = buildRows(sections, registry as never, {}, emitInfo, stageLabels, TEMPLATE)
    const identity = rows.find((r) => r.key === 'identity')
    expect(identity?.emitText).toBe('你是云枢。')
    expect(identity?.emitted).toBe(true)
  })

  it('子节（body_status）启用后可立即给出占位符内容', () => {
    const enabledBody = { ...sections, body_status: { ...sections.body_status, enabled: true } }
    const rows = buildRows(enabledBody, registry as never, {}, emitInfo, stageLabels, TEMPLATE)
    const body = rows.find((r) => r.key === 'body_status')
    expect(body?.enabled).toBe(true)
    expect(body?.emitText).toBe('{body_status}')
  })
})

describe('buildDisplayOrder', () => {
  it('registry 顺序优先，配置里多余/未覆盖的节补在后面', () => {
    const order = buildDisplayOrder(
      { ...sections, orphan: { key: 'orphan', enabled: true } },
      registry as never,
    )
    expect(order.slice(0, 3)).toEqual(['identity', 'principles', 'skill_instructions'])
    expect(order).toContain('orphan')
    expect(order.indexOf('orphan')).toBeGreaterThan(order.indexOf('memory_context'))
  })
})
